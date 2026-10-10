"""公司事实投影：主库只读，独立 SQLite 按来源批次提交，可离线重建。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import storage
from .company_parser import (
    DATASETS,
    PARSER_VERSION,
    CompanyDataError,
    ParsedCompanyData,
    parse_company_archive,
)
from .models import Source, validate_ticker

SCHEMA_VERSION = 1
_DDL = (
    """CREATE TABLE source_batches (
        id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        ticker TEXT NOT NULL, dataset TEXT NOT NULL, provider TEXT NOT NULL,
        parser_version TEXT NOT NULL, raw_path TEXT NOT NULL, retrieved_at TEXT NOT NULL,
        source_identity_json TEXT NOT NULL, indexed_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('completed','partial','failed')),
        profile_count INTEGER NOT NULL, financial_count INTEGER NOT NULL,
        diagnostics_json TEXT NOT NULL,
        UNIQUE(source_id, source_sha256, parser_version))""",
    """CREATE TABLE company_profile_facts (
        batch_id INTEGER NOT NULL REFERENCES source_batches(id),
        field_key TEXT NOT NULL, label TEXT NOT NULL, value_json TEXT NOT NULL,
        value_type TEXT NOT NULL, effective_at TEXT, locator TEXT NOT NULL,
        PRIMARY KEY(batch_id, locator))""",
    """CREATE TABLE financial_facts (
        batch_id INTEGER NOT NULL REFERENCES source_batches(id),
        field_key TEXT NOT NULL, label TEXT NOT NULL, value_text TEXT NOT NULL,
        value_decimal TEXT NOT NULL, period TEXT, period_kind TEXT NOT NULL,
        period_start TEXT, period_end TEXT, currency TEXT, unit TEXT, scale TEXT,
        accounting_standard TEXT, accounting_scope TEXT, published_at TEXT,
        dimensions_json TEXT NOT NULL, locator TEXT NOT NULL,
        PRIMARY KEY(batch_id, locator))""",
    "CREATE INDEX batches_company ON source_batches(ticker, dataset, parser_version, status)",
    "CREATE INDEX financial_field_period ON financial_facts(field_key, period, batch_id)",
    "CREATE INDEX profile_field ON company_profile_facts(field_key, batch_id)",
)


class CompanyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str
    dataset: str | None = None
    field: str | None = None
    period: str | None = None
    source_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @field_validator("ticker")
    @classmethod
    def ticker_valid(cls, value: str) -> str:
        return validate_ticker(value)

    @field_validator("dataset")
    @classmethod
    def dataset_valid(cls, value: str | None) -> str | None:
        if value is not None and value not in DATASETS:
            raise ValueError("不支持的公司数据集")
        return value


def database_path(root: Path) -> Path:
    root = root.resolve()
    path = (root / "data" / "company.db").resolve()
    if path != root / "data" / "company.db":
        raise CompanyDataError("company_unsafe_path", "公司数据库路径越出项目 data")
    return path


@contextmanager
def _readonly(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.is_file():
        raise CompanyDataError("company_database_missing", "数据库不存在，请先运行 company-data sync")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        yield connection


def _check_version(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        raise CompanyDataError("company_schema_unsupported", "公司库 schema 版本不支持")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                raise CompanyDataError("company_schema_unsupported", "拒绝覆盖未知数据库")
            with storage.transaction(connection):
                for ddl in _DDL:
                    connection.execute(ddl)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        _check_version(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _source_rows(root: Path, ticker: str | None) -> list[dict[str, Any]]:
    path = storage.db_path(root).resolve()
    if not path.is_relative_to(root.resolve() / "data"):
        raise CompanyDataError("company_unsafe_path", "来源数据库路径越出项目 data")
    with _readonly(path) as connection:
        rows = connection.execute(
            "SELECT * FROM sources WHERE (? IS NULL OR ticker=?) ORDER BY retrieved_at,id",
            (ticker, ticker),
        ).fetchall()
    selected = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"])
        except (ValueError, TypeError) as error:
            raise CompanyDataError("company_source_invalid", "来源 metadata 不是有效 JSON") from error
        if not isinstance(metadata, dict):
            raise CompanyDataError("company_source_invalid", "来源 metadata 不是对象")
        if metadata.get("provider") == "futu" and metadata.get("dataset") in DATASETS:
            selected.append(dict(row))
    return selected


def _identity(row: dict[str, Any]) -> tuple[Source, str]:
    try:
        source = Source.model_validate_json(row["source_json"])
    except ValidationError as error:
        raise CompanyDataError("company_source_invalid", "来源快照不符合 Source 协议") from error
    try:
        validate_ticker(source.ticker)
    except ValueError as error:
        raise CompanyDataError("company_source_invalid", "来源股票代码不合法") from error
    for column, value in (
        ("id", str(source.id)), ("ticker", source.ticker), ("sha256", source.sha256),
        ("raw_path", source.raw_path), ("kind", source.kind),
        ("retrieved_at", storage.to_iso(source.retrieved_at)),
    ):
        if row[column] != value:
            raise CompanyDataError("company_source_mismatch", "来源登记与快照身份不一致")
    if source.kind != "provider":
        raise CompanyDataError("company_source_mismatch", "财务投影需要 provider 来源")
    metadata = json.loads(row["metadata_json"])
    if any(metadata.get(k) != source.metadata.get(k) for k in ("provider", "dataset")):
        raise CompanyDataError("company_source_mismatch", "来源数据集与快照不一致")
    return source, source.model_dump_json()


def _archive(root: Path, source: Source) -> bytes:
    path = (root / source.raw_path).resolve()
    allowed = root.resolve() / "data" / "stocks" / source.ticker
    if not path.is_relative_to(allowed):
        raise CompanyDataError("company_unsafe_path", "公司来源不在当前 ticker 的归档目录内")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CompanyDataError("company_source_unreadable", "无法读取公司原始归档") from error
    if hashlib.sha256(raw).hexdigest() != source.sha256:
        raise CompanyDataError("company_source_hash_mismatch", "公司原始归档哈希不匹配")
    return raw


def _insert_facts(connection: sqlite3.Connection, batch_id: int, parsed: ParsedCompanyData) -> None:
    for table, facts in (
        ("company_profile_facts", parsed.profiles), ("financial_facts", parsed.financials),
    ):
        if not facts:
            continue
        columns = list(type(facts[0]).model_fields)
        connection.executemany(
            f"INSERT INTO {table}(batch_id,{','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in range(len(columns) + 1))})",
            [(batch_id, *fact.model_dump().values()) for fact in facts],
        )


def _batch(
    connection: sqlite3.Connection, row: dict[str, Any], identity: str,
    parsed: ParsedCompanyData, status: str,
) -> None:
    with storage.transaction(connection):
        connection.execute(
            """INSERT INTO source_batches (
                source_id,source_sha256,ticker,dataset,provider,parser_version,raw_path,
                retrieved_at,source_identity_json,indexed_at,status,profile_count,
                financial_count,diagnostics_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_id,source_sha256,parser_version) DO UPDATE SET
                source_identity_json=excluded.source_identity_json,indexed_at=excluded.indexed_at,
                status=excluded.status,profile_count=excluded.profile_count,
                financial_count=excluded.financial_count,diagnostics_json=excluded.diagnostics_json""",
            (row["id"], row["sha256"], row["ticker"], json.loads(row["metadata_json"])["dataset"],
             "futu", PARSER_VERSION, row["raw_path"], row["retrieved_at"], identity,
             storage.to_iso(storage.now_utc()), status, len(parsed.profiles), len(parsed.financials),
             json.dumps(parsed.diagnostics, ensure_ascii=False)),
        )
        batch_id = connection.execute(
            "SELECT id FROM source_batches WHERE source_id=? AND source_sha256=? AND parser_version=?",
            (row["id"], row["sha256"], PARSER_VERSION),
        ).fetchone()[0]
        connection.execute("DELETE FROM company_profile_facts WHERE batch_id=?", (batch_id,))
        connection.execute("DELETE FROM financial_facts WHERE batch_id=?", (batch_id,))
        _insert_facts(connection, batch_id, parsed)


def sync(
    root: Path, ticker: str | None = None, *, rebuild: bool = False,
    lock_timeout: float = storage.LOCK_TIMEOUT_SECONDS,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """不迁移/写主库；失败批次可重试，重建有错误时不替换原公司库。"""
    if ticker is not None:
        try:
            ticker = validate_ticker(ticker)
        except ValueError as error:
            raise CompanyDataError("invalid_ticker", "股票代码不合法") from error
    if rebuild and (ticker is not None or source_ids is not None):
        raise CompanyDataError("company_rebuild_scope", "重建必须包含全部公司来源")
    path = database_path(root)
    result: dict[str, Any] = {
        "database": "data/company.db", "parser_version": PARSER_VERSION, "rebuild": rebuild,
        "selected": 0, "indexed": 0, "skipped": 0, "partial": 0, "failed": 0,
        "profile_facts": 0, "financial_facts": 0, "issues": [], "activated": False,
    }
    temporary: Path | None = None
    with storage.project_lock(root, timeout=lock_timeout):
        rows = _source_rows(root, ticker)
        if source_ids is not None:
            selected_ids = set(source_ids)
            rows = [row for row in rows if row["id"] in selected_ids]
            if {row["id"] for row in rows} != selected_ids:
                raise CompanyDataError("company_source_missing", "指定的公司来源不存在或数据集不支持")
        result["selected"] = len(rows)
        if rebuild:
            fd, name = tempfile.mkstemp(prefix=".company-rebuild-", suffix=".db", dir=path.parent)
            os.close(fd)
            temporary = Path(name)
        try:
            with closing(_connect(temporary or path)) as connection:
                for row in rows:
                    identity = row["source_json"]
                    try:
                        source, identity = _identity(row)
                        raw = _archive(root, source)
                        existing = connection.execute(
                            """SELECT status,source_identity_json FROM source_batches
                            WHERE source_id=? AND source_sha256=? AND parser_version=?""",
                            (row["id"], row["sha256"], PARSER_VERSION),
                        ).fetchone()
                        if (existing and existing["status"] in {"completed", "partial"}
                                and existing["source_identity_json"] == identity):
                            result["skipped"] += 1
                            continue
                        parsed = parse_company_archive(raw, source.metadata["dataset"])
                        status = "partial" if parsed.diagnostics else "completed"
                    except CompanyDataError as error:
                        parsed = ParsedCompanyData(diagnostics=[{"code": error.code}])
                        status = "failed"
                        result["failed"] += 1
                        result["issues"].append({
                            "code": error.code, "source_id": row["id"], "message": error.message,
                        })
                    _batch(connection, row, identity, parsed, status)
                    if status != "failed":
                        result["indexed"] += 1
                        result["partial"] += int(status == "partial")
                        result["profile_facts"] += len(parsed.profiles)
                        result["financial_facts"] += len(parsed.financials)
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise CompanyDataError("company_integrity_failed", "公司库完整性检查失败")
            if temporary is not None and not result["failed"]:
                os.replace(temporary, path)
                temporary = None
            result["activated"] = not rebuild or not result["failed"]
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return result


def status(root: Path, ticker: str | None = None) -> dict[str, Any]:
    if ticker is not None:
        CompanyQuery(ticker=ticker)
    path = database_path(root)
    if not path.is_file():
        return {"database": "data/company.db", "initialized": False, "coverage": []}
    with _readonly(path) as connection:
        _check_version(connection)
        coverage = connection.execute(
            """SELECT ticker,dataset,status,COUNT(*) AS source_batches,
                SUM(profile_count) AS profile_facts,SUM(financial_count) AS financial_facts
            FROM source_batches WHERE parser_version=? AND (? IS NULL OR ticker=?)
            GROUP BY ticker,dataset,status ORDER BY ticker,dataset,status""",
            (PARSER_VERSION, ticker, ticker),
        ).fetchall()
        diagnostics = connection.execute(
            """SELECT source_id,ticker,dataset,status,diagnostics_json FROM source_batches
            WHERE parser_version=? AND status!='completed' AND (? IS NULL OR ticker=?)
            ORDER BY ticker,dataset,source_id LIMIT 51""",
            (PARSER_VERSION, ticker, ticker),
        ).fetchall()
    return {
        "database": "data/company.db", "initialized": True, "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION, "coverage": [dict(row) for row in coverage],
        "diagnostics": [dict(row) for row in diagnostics[:50]],
        "diagnostics_has_more": len(diagnostics) > 50,
    }


def read(root: Path, query: CompanyQuery, *, fields: bool = False) -> dict[str, Any]:
    """人工检查入口：显示全部已索引版本，明确不冒充 company_latest 或历史时点视图。"""
    tables = ("company_profile_facts",) if query.dataset == "profile" else (
        ("financial_facts",) if query.dataset else ("company_profile_facts", "financial_facts")
    )
    where = """b.ticker=? AND b.parser_version=? AND b.status IN ('completed','partial')
        AND (? IS NULL OR b.dataset=?) AND (? IS NULL OR b.source_id=?)
        AND (? IS NULL OR f.field_key=? OR f.label=?)"""
    params = [query.ticker, PARSER_VERSION, query.dataset, query.dataset,
              query.source_id, query.source_id, query.field, query.field, query.field]
    results = []
    with _readonly(database_path(root)) as connection:
        _check_version(connection)
        for table in tables:
            clause = where
            values = params[:]
            if query.period is not None:
                if table == "company_profile_facts":
                    continue
                clause += " AND f.period=?"
                values.append(query.period)
            if fields:
                selection = "b.dataset,f.field_key,f.label,COUNT(*) AS fact_count"
                group = "GROUP BY b.dataset,f.field_key,f.label"
                order = "b.dataset,f.field_key,f.label"
            else:
                selection = """f.*,b.source_id,b.source_sha256,b.ticker,b.dataset,b.raw_path,
                    b.retrieved_at,b.parser_version,b.status AS batch_status"""
                group = ""
                order = "b.retrieved_at DESC,b.source_id,f.locator"
            rows = connection.execute(
                f"SELECT {selection} FROM {table} f JOIN source_batches b ON b.id=f.batch_id "
                f"WHERE {clause} {group} ORDER BY {order} LIMIT ? OFFSET ?",
                (*values, query.limit + 1, query.offset),
            ).fetchall()
            results.append({
                "table": table, "rows": [dict(row) for row in rows[:query.limit]],
                "has_more": len(rows) > query.limit,
            })
    return {
        "selection": "all_indexed_sources", "parser_version": PARSER_VERSION,
        "limit_per_table": query.limit, "offset": query.offset, "results": results,
        "note": "人工检查所有来源版本；未进行最新来源、历史时点或本 Turn 证据授权选择",
    }


def backup_database(root: Path, destination: Path) -> dict[str, Any] | None:
    """由持有项目写锁的 backup 调用；不存在则保持旧项目兼容。"""
    path = database_path(root)
    if not path.is_file():
        return None
    with _readonly(path) as source, closing(sqlite3.connect(destination)) as target:
        _check_version(source)
        source.backup(target)
    return {
        "path": destination.name, "sha256": storage.sha256_file(destination),
        "schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION,
    }
