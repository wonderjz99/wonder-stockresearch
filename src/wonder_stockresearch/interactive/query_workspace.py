"""阶段 19C 的隔离 QueryWorkspace。

模型永远不会连接项目主数据库：每次 SQL 调用都先从当前 run 的已归档数值构造一个
最小 SQLite 快照，再在 immutable/read-only 连接上执行一条受限查询。临时函数也只接收
对应 SQL 的 JSON 行，不接触文件、环境变量、网络或项目服务。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import resource
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from wonder_stockresearch import financial_calculations, storage
from wonder_stockresearch.interactive import agent_company
from wonder_stockresearch.models import Source

MAX_FACTS = 10_000
MAX_SQL_CHARS = 12_000
MAX_SQL_ROWS = 400
MAX_SQL_ATTEMPTS_PER_TURN = 6
MAX_FUNCTION_EXECUTIONS_PER_TURN = 3
MAX_FUNCTION_CHARS = 12_000
MAX_OUTPUT_BYTES = 64 * 1024
TIMEOUT_SECONDS = 10
MEMORY_BYTES = 256 * 1024 * 1024
MAX_SQL_PROGRESS_CALLBACKS = 100

_ALLOWED_TABLES = frozenset({"financial_facts", "company_profile_facts", "source_metadata"})
_FORBIDDEN_SQL_TOKENS = re.compile(
    r"\b(?:alter|analyze|attach|begin|commit|create|delete|detach|drop|end|insert|"
    r"pragma|reindex|release|rollback|savepoint|transaction|update|vacuum)\b",
    re.IGNORECASE,
)


class QueryWorkspaceError(Exception):
    """可安全回送给 Agent 的 QueryWorkspace 错误。"""

    def __init__(self, code: str, message: str, *, exit_code: int = 2) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_models(
    connection: sqlite3.Connection,
    run_id: str | None,
    ticker: str,
    session_id: str = "",
    turn_id: str | None = None,
) -> list[Source]:
    rows = agent_company.context_sources(
        connection, {"ticker": ticker, "run_id": run_id, "id": session_id, "turn_id": turn_id}
    )
    return [Source.model_validate_json(row["source_json"]) for row in rows]


def _turn_for_row(root: Path, row: dict[str, Any], turn_id: str) -> dict[str, Any]:
    session_id = str(row["id"])
    with storage.open_db(root) as connection:
        turn = storage.get_agent_turn(connection, turn_id)
    if turn is None or turn["session_id"] != session_id or turn["ticker"] != row["ticker"]:
        raise QueryWorkspaceError("unknown_agent_turn", "QueryWorkspace 不属于当前 Agent session")
    if turn.get("run_id") != row.get("run_id"):
        raise QueryWorkspaceError("turn_run_mismatch", "QueryWorkspace 的 run 上下文已变化")
    return turn


def _snapshot_payload(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    ticker = str(row["ticker"])
    run_id = str(row["run_id"]) if row.get("run_id") else None
    company = agent_company.facts(root, row)
    if company is not None:
        total = sum(len(company[table]) for table in ("financial_facts", "company_profile_facts"))
        if total > MAX_FACTS:
            raise QueryWorkspaceError(
                "analysis_snapshot_too_large", "公司事实快照超过 10,000 条限制"
            )
        with storage.open_db(root) as connection:
            sources = agent_company.context_sources(connection, row)
        for table in ("financial_facts", "company_profile_facts"):
            company[table] = [{"fact_id": _canonical_hash(item), **item} for item in company[table]]
            company[table].sort(key=lambda item: item["fact_id"])
        metadata = []
        for source in sorted(sources, key=lambda item: item["id"]):
            model = Source.model_validate_json(source["source_json"])
            metadata.append(
                {
                    "source_id": source["id"],
                    "source_sha256": source["sha256"],
                    "kind": model.kind,
                    "title": model.title,
                    "retrieved_at": source["retrieved_at"],
                    "dataset": model.metadata.get("dataset"),
                    "parser_version": next(
                        (
                            item["parser_version"]
                            for item in company["coverage"]
                            if item.get("source_id") == source["id"]
                        ),
                        None,
                    ),
                }
            )
        return {
            "schema_version": 2,
            "ticker": ticker,
            "run_id": run_id,
            "source_metadata": metadata,
            **company,
        }
    with storage.open_db(root) as connection:
        sources = _source_models(
            connection, run_id, ticker, str(row.get("id", "")), row.get("turn_id")
        )
    by_source = {str(source.id): source for source in sources}
    numeric_inputs = [
        *financial_calculations.numeric_inputs(sources, root),
        *financial_calculations.annual_capex_inputs(root, sources),
    ]
    facts: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for item in numeric_inputs:
        source = by_source.get(item.source_id)
        if source is None:
            continue
        payload = {
            "label": item.label,
            "value": str(item.value),
            "period": item.period,
            "currency": item.currency,
            "unit": item.unit,
            "source_id": item.source_id,
            "locator": item.locator,
            "source_sha256": source.sha256,
        }
        fact_id = _canonical_hash(payload)
        if fact_id in seen:
            continue
        seen.add(fact_id)
        facts.append({"fact_id": fact_id, **payload})
    facts.sort(
        key=lambda item: (
            str(item["period"]),
            str(item["label"]),
            str(item["source_id"]),
            str(item["locator"]),
        )
    )
    if not facts:
        raise QueryWorkspaceError(
            "analysis_snapshot_empty", "当前归档资料没有可查询的结构化财务事实"
        )
    if len(facts) > MAX_FACTS:
        raise QueryWorkspaceError("analysis_snapshot_too_large", "财务事实快照超过 10,000 条限制")
    source_metadata = [
        {
            "source_id": str(source.id),
            "source_sha256": source.sha256,
            "kind": source.kind,
            "title": source.title,
        }
        for source in sorted(sources, key=lambda item: str(item.id))
    ]
    return {
        "schema_version": 1,
        "ticker": ticker,
        "run_id": run_id,
        "financial_facts": facts,
        "source_metadata": source_metadata,
    }


def _schema_description(fact_count: int) -> dict[str, object]:
    return {
        "snapshot_schema_version": 1,
        "fact_count": fact_count,
        "tables": {
            "financial_facts": [
                "fact_id",
                "label",
                "value_decimal",
                "period",
                "currency",
                "unit",
                "source_id",
                "locator",
                "source_sha256",
            ],
            "source_metadata": ["source_id", "source_sha256", "kind", "title"],
        },
        "rules": [
            "只允许一条 SELECT 或 WITH ... SELECT。",
            "value_decimal 保留原始 Decimal 文本；需要数值运算时由 SQL 显式 CAST。",
            "只有结果直接包含 fact_id 时，程序才返回可引用来源。",
        ],
    }


_COMPANY_COLUMNS = {
    "financial_facts": [
        "fact_id",
        "field_key",
        "label",
        "value_text",
        "value_decimal",
        "period",
        "period_kind",
        "period_start",
        "period_end",
        "currency",
        "unit",
        "scale",
        "accounting_standard",
        "accounting_scope",
        "published_at",
        "dimensions_json",
        "locator",
        "source_id",
        "source_sha256",
        "dataset",
    ],
    "company_profile_facts": [
        "fact_id",
        "field_key",
        "label",
        "value_json",
        "value_type",
        "effective_at",
        "locator",
        "source_id",
        "source_sha256",
        "dataset",
    ],
    "source_metadata": [
        "source_id",
        "source_sha256",
        "kind",
        "title",
        "retrieved_at",
        "dataset",
        "parser_version",
    ],
}


def _snapshot_schema(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = _schema_description(len(snapshot["financial_facts"]))
    if snapshot["schema_version"] == 2:
        result.update(
            snapshot_schema_version=2,
            tables=_COMPANY_COLUMNS,
            profile_count=len(snapshot["company_profile_facts"]),
            coverage=snapshot["coverage"],
        )
        result["rules"].extend(
            [
                "period_kind、dimensions_json、unit、currency、scale、accounting_scope 保留原口径；未知为 NULL。",
                "statements 的 data/yoy/qoq 是不同 field_key；求平均前必须选择同一字段和口径。",
                "company_profile_facts.value_json 为原始类型的 JSON 文本；字段目录本身不能引用。",
            ]
        )
    return result


def describe_schema(root: Path, row: dict[str, Any], turn_id: str) -> dict[str, object]:
    _turn_for_row(root, row, turn_id)
    snapshot = _snapshot_payload(root, {**row, "turn_id": turn_id})
    description = _snapshot_schema(snapshot)
    return {
        "turn_id": turn_id,
        "ticker": snapshot["ticker"],
        "run_id": snapshot["run_id"],
        "snapshot_hash": _canonical_hash(snapshot),
        **description,
    }


def validate_sql(sql: str) -> None:
    if not sql.strip() or len(sql) > MAX_SQL_CHARS:
        raise QueryWorkspaceError("invalid_analysis_sql", "SQL 为空或超过 12,000 字符限制")
    if "\x00" in sql or ";" in sql:
        raise QueryWorkspaceError("analysis_sql_not_allowed", "SQL 只能是一条语句，不能包含分号")
    first = re.match(r"\s*([A-Za-z]+)", sql)
    if first is None or first.group(1).casefold() not in {"select", "with"}:
        raise QueryWorkspaceError("analysis_sql_not_allowed", "SQL 只能以 SELECT 或 WITH 开始")
    if _FORBIDDEN_SQL_TOKENS.search(sql) or re.search(r"\brecursive\b", sql, re.IGNORECASE):
        raise QueryWorkspaceError(
            "analysis_sql_not_allowed", "SQL 包含不允许的写入、管理或递归语句"
        )


def _create_snapshot(path: Path, payload: dict[str, Any]) -> None:
    connection = sqlite3.connect(path)
    try:
        if payload["schema_version"] == 2:
            for table, columns in _COMPANY_COLUMNS.items():
                definitions = [
                    f"{column} TEXT"
                    + (
                        " PRIMARY KEY"
                        if column in {"fact_id", "source_id"} and column == columns[0]
                        else ""
                    )
                    for column in columns
                ]
                connection.execute(f"CREATE TABLE {table} ({','.join(definitions)})")
                connection.executemany(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    [tuple(item.get(column) for column in columns) for item in payload[table]],
                )
            connection.commit()
            return
        connection.execute(
            """
            CREATE TABLE financial_facts (
                fact_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                value_decimal TEXT NOT NULL,
                period TEXT NOT NULL,
                currency TEXT,
                unit TEXT,
                source_id TEXT NOT NULL,
                locator TEXT NOT NULL,
                source_sha256 TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE source_metadata (
                source_id TEXT PRIMARY KEY,
                source_sha256 TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO financial_facts(
                fact_id, label, value_decimal, period, currency, unit, source_id, locator, source_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["fact_id"],
                    item["label"],
                    item["value"],
                    item["period"],
                    item["currency"],
                    item["unit"],
                    item["source_id"],
                    item["locator"],
                    item["source_sha256"],
                )
                for item in payload["financial_facts"]
            ],
        )
        connection.executemany(
            """
            INSERT INTO source_metadata(source_id, source_sha256, kind, title)
            VALUES (?, ?, ?, ?)
            """,
            [
                (item["source_id"], item["source_sha256"], item["kind"], item["title"])
                for item in payload["source_metadata"]
            ],
        )
        connection.commit()
    finally:
        connection.close()
        path.chmod(0o444)


def _workspace(root: Path, session_id: str, turn_id: str, analysis_id: str) -> Path:
    return root / "data" / "tmp" / "analysis" / session_id / turn_id / analysis_id


def _remaining_sql_budget(root: Path, session_id: str, turn_id: str) -> int:
    with storage.open_db(root) as connection:
        runs = storage.list_analysis_runs(connection, session_id, turn_id=turn_id)
    return MAX_SQL_ATTEMPTS_PER_TURN - sum(1 for item in runs if item.get("sql_hash"))


def _prepare_sql_run(
    root: Path, row: dict[str, Any], turn_id: str, sql: str
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    validate_sql(sql)
    _turn_for_row(root, row, turn_id)
    session_id = str(row["id"])
    if _remaining_sql_budget(root, session_id, turn_id) <= 0:
        raise QueryWorkspaceError("analysis_sql_limit", "每个 Turn 最多执行六次 SQL")
    snapshot = _snapshot_payload(root, {**row, "turn_id": turn_id})
    analysis_id = str(uuid4())
    workspace = _workspace(root, session_id, turn_id, analysis_id)
    workspace.mkdir(parents=True, exist_ok=False)
    snapshot_path = workspace / "snapshot.db"
    _create_snapshot(snapshot_path, snapshot)
    schema = _snapshot_schema(snapshot)
    storage.write_atomic(
        workspace / "schema.json", json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    )
    storage.write_atomic(workspace / "query.sql", sql + "\n")
    (workspace / "schema.json").chmod(0o444)
    (workspace / "query.sql").chmod(0o444)
    sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    limits = {
        "timeout_seconds": TIMEOUT_SECONDS,
        "memory_bytes": MEMORY_BYTES,
        "max_output_bytes": MAX_OUTPUT_BYTES,
        "max_sql_rows": MAX_SQL_ROWS,
        "max_sql_attempts_per_turn": MAX_SQL_ATTEMPTS_PER_TURN,
        "max_function_executions_per_turn": MAX_FUNCTION_EXECUTIONS_PER_TURN,
    }
    with storage.project_lock(root), storage.open_db(root) as connection:
        if (
            len(
                [
                    item
                    for item in storage.list_analysis_runs(connection, session_id, turn_id=turn_id)
                    if item.get("sql_hash")
                ]
            )
            >= MAX_SQL_ATTEMPTS_PER_TURN
        ):
            raise QueryWorkspaceError("analysis_sql_limit", "每个 Turn 最多执行六次 SQL")
        storage.insert_analysis_run(
            connection,
            analysis_id=analysis_id,
            session_id=session_id,
            turn_id=turn_id,
            ticker=str(row["ticker"]),
            execution_index=storage.next_analysis_execution_index(connection, session_id, turn_id),
            status="sql_running",
            dataset_hash=_canonical_hash(snapshot),
            code_hash=None,
            sql_hash=sql_hash,
            limits_json=json.dumps(limits, ensure_ascii=False, sort_keys=True),
            source_refs_json="[]",
            workspace_path=storage.relative_path(root, workspace),
            created_at=storage.now_utc(),
        )
    return {"analysis_id": analysis_id, "sql_hash": sql_hash}, workspace, snapshot


_SQL_RUNNER = r"""
import json, sqlite3, sys

payload = json.loads(sys.stdin.read())
allowed_tables = {"financial_facts", "company_profile_facts", "source_metadata"}
denied = {
    sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ANALYZE, sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW, sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DELETE, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_VIEW, sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_PRAGMA,
    sqlite3.SQLITE_REINDEX, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_UPDATE,
}
callbacks = 0

def authorizer(action, arg1, arg2, database, trigger):
    if action in denied:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and arg1 not in allowed_tables:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or arg1 or "").casefold() == "load_extension":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK

def interrupt():
    global callbacks
    callbacks += 1
    return 1 if callbacks > payload["max_progress_callbacks"] else 0

def reply(value, status=0):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, default=str))
    sys.exit(status)

connection = sqlite3.connect(payload["uri"], uri=True)
connection.row_factory = sqlite3.Row
try:
    connection.execute("PRAGMA query_only = ON")
    connection.set_authorizer(authorizer)
    connection.set_progress_handler(interrupt, 1000)
    cursor = connection.execute(payload["sql"])
    names = [str(item[0]) for item in cursor.description or ()]
    if not names or len(names) != len(set(names)):
        reply({"ok": False, "code": "analysis_sql_columns_invalid"}, 2)
    rows = [dict(item) for item in cursor.fetchmany(payload["max_rows"] + 1)]
    if len(rows) > payload["max_rows"]:
        reply({"ok": False, "code": "analysis_sql_row_limit"}, 2)
    reply({"ok": True, "rows": rows})
except sqlite3.Error as error:
    code = "analysis_sql_timeout" if "interrupted" in str(error).casefold() else "analysis_sql_failed"
    reply({"ok": False, "code": code, "diagnostic": str(error)[:200]}, 2)
finally:
    connection.close()
"""


def _execute_sql(snapshot_path: Path, sql: str) -> list[dict[str, Any]]:
    uri = f"{snapshot_path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _SQL_RUNNER],
            input=json.dumps(
                {
                    "uri": uri,
                    "sql": sql,
                    "max_rows": MAX_SQL_ROWS,
                    "max_progress_callbacks": MAX_SQL_PROGRESS_CALLBACKS,
                },
                ensure_ascii=False,
            ),
            text=True,
            capture_output=True,
            cwd=snapshot_path.parent,
            env={"PATH": os.defpath, "LC_ALL": "C"},
            timeout=TIMEOUT_SECONDS,
            preexec_fn=_limit_resources,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise QueryWorkspaceError("analysis_sql_timeout", "SQL 超过十秒限制") from error
    if len(completed.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise QueryWorkspaceError("analysis_output_too_large", "SQL 结果超过 64 KB 限制")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise QueryWorkspaceError("analysis_sql_failed", "SQL 子进程未返回有效结果") from error
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        code = payload.get("code") if isinstance(payload, dict) else None
        if code == "analysis_sql_timeout":
            raise QueryWorkspaceError("analysis_sql_timeout", "SQL 超出受限执行步数")
        if code == "analysis_sql_columns_invalid":
            raise QueryWorkspaceError("analysis_sql_columns_invalid", "SQL 结果必须含唯一的列名")
        if code == "analysis_sql_row_limit":
            raise QueryWorkspaceError("analysis_sql_row_limit", "SQL 最多返回 400 行")
        diagnostic = str(payload.get("diagnostic", ""))[:200] if isinstance(payload, dict) else ""
        raise QueryWorkspaceError("analysis_sql_failed", f"隔离 SQLite 拒绝 SQL：{diagnostic}")
    rows = payload.get("rows")
    if (
        completed.returncode != 0
        or not isinstance(rows, list)
        or not all(isinstance(item, dict) for item in rows)
    ):
        raise QueryWorkspaceError("analysis_sql_failed", "SQL 子进程执行失败")
    return rows


def _citable_refs(snapshot_path: Path, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    fact_ids = sorted(
        {value for row in rows if isinstance((value := row.get("fact_id")), str) and value}
    )
    if not fact_ids:
        return []
    uri = f"{snapshot_path.resolve().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        placeholders = ",".join("?" for _ in fact_ids)
        result = connection.execute(
            f"""
            SELECT fact_id, source_id, locator, source_sha256
            FROM financial_facts WHERE fact_id IN ({placeholders})
            """,
            fact_ids,
        ).fetchall()
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='company_profile_facts'"
        ).fetchone():
            result.extend(
                connection.execute(
                    f"SELECT fact_id,source_id,locator,source_sha256 FROM company_profile_facts WHERE fact_id IN ({placeholders})",
                    fact_ids,
                ).fetchall()
            )
    finally:
        connection.close()
    return [
        {
            "fact_id": str(item[0]),
            "source_id": str(item[1]),
            "locator": str(item[2]),
            "relation": "supports",
            "source_sha256": str(item[3]),
        }
        for item in result
    ]


def _finish_failure(root: Path, analysis_id: str, status: str, message: str) -> None:
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.finish_analysis_run(
            connection,
            analysis_id,
            status=status,
            result_json=None,
            result_summary=message[:2000],
            finished_at=storage.now_utc(),
        )


def run_readonly_sql(root: Path, row: dict[str, Any], turn_id: str, sql: str) -> dict[str, Any]:
    prepared, workspace, _snapshot = _prepare_sql_run(root, row, turn_id, sql)
    analysis_id = str(prepared["analysis_id"])
    try:
        rows = _execute_sql(workspace / "snapshot.db", sql)
        refs = _citable_refs(workspace / "snapshot.db", rows)
        result = {
            "analysis_id": analysis_id,
            "rows": rows,
            "row_count": len(rows),
            "citable": bool(refs),
            "citable_refs": refs,
        }
        rendered = json.dumps(result, ensure_ascii=False, allow_nan=False, default=str)
        if len(rendered.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise QueryWorkspaceError("analysis_output_too_large", "SQL 结果超过 64 KB 限制")
        storage.write_atomic(workspace / "sql-result.json", rendered + "\n")
        (workspace / "sql-result.json").chmod(0o444)
        source_refs = [
            {key: item[key] for key in ("source_id", "locator", "relation")} for item in refs
        ]
        with storage.project_lock(root), storage.open_db(root) as connection:
            storage.finish_analysis_run(
                connection,
                analysis_id,
                status="sql_succeeded",
                result_json=rendered,
                result_summary=f"SQL 返回 {len(rows)} 行；可引用事实 {len(refs)} 条",
                finished_at=storage.now_utc(),
            )
            connection.execute(
                "UPDATE analysis_runs SET source_refs_json = ? WHERE id = ?",
                (json.dumps(source_refs, ensure_ascii=False), analysis_id),
            )
        return {
            "analysis_id": analysis_id,
            "status": "sql_succeeded",
            "dataset_hash": _canonical_hash(_snapshot),
            "sql_hash": prepared["sql_hash"],
            **result,
        }
    except QueryWorkspaceError as error:
        _finish_failure(root, analysis_id, "sql_failed", error.message)
        raise


_ALLOWED_FUNCTION_NODES = {
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.Expr,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.If,
    ast.IfExp,
    ast.For,
    ast.Break,
    ast.Continue,
    ast.Subscript,
    ast.Slice,
    ast.Call,
    ast.keyword,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
}
_ALLOWED_FUNCTION_CALLS = {
    "abs",
    "enumerate",
    "float",
    "int",
    "len",
    "max",
    "min",
    "range",
    "round",
    "sorted",
    "str",
    "sum",
}


def _assignment_target_is_allowed(target: ast.expr) -> bool:
    if isinstance(target, (ast.Tuple, ast.List)):
        return all(_assignment_target_is_allowed(item) for item in target.elts)
    return isinstance(target, ast.Name) or (
        isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
    )


def validate_function(code: str) -> None:
    if not code.strip() or len(code) > MAX_FUNCTION_CHARS:
        raise QueryWorkspaceError("invalid_analysis_function", "临时函数为空或超过长度上限")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as error:
        raise QueryWorkspaceError(
            "invalid_analysis_function", f"临时函数语法错误：{error.msg}"
        ) from error
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise QueryWorkspaceError(
            "analysis_function_not_allowed", "只能定义一个 def analyze(rows) 函数"
        )
    function = tree.body[0]
    if (
        function.name != "analyze"
        or function.decorator_list
        or len(function.args.args) != 1
        or function.args.args[0].arg != "rows"
        or function.args.vararg is not None
        or function.args.kwarg is not None
        or function.args.kwonlyargs
    ):
        raise QueryWorkspaceError(
            "analysis_function_not_allowed", "函数签名必须是 def analyze(rows)"
        )
    if not any(isinstance(item, ast.Return) for item in ast.walk(function)):
        raise QueryWorkspaceError(
            "analysis_function_result_missing", "临时函数必须 return JSON 对象"
        )
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_FUNCTION_NODES:
            raise QueryWorkspaceError(
                "analysis_function_not_allowed", f"不允许的 Python 语法：{type(node).__name__}"
            )
        if isinstance(node, ast.Attribute):
            raise QueryWorkspaceError("analysis_function_not_allowed", "不允许属性访问")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or "__" in node.id):
            raise QueryWorkspaceError("analysis_function_not_allowed", "不允许 dunder 名称")
        if isinstance(node, ast.FunctionDef) and node is not function:
            raise QueryWorkspaceError("analysis_function_not_allowed", "不允许嵌套函数或递归")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not all(_assignment_target_is_allowed(target) for target in targets):
                raise QueryWorkspaceError(
                    "analysis_function_not_allowed", "赋值目标只能是局部变量或字典项"
                )
        if isinstance(node, ast.For) and not _assignment_target_is_allowed(node.target):
            raise QueryWorkspaceError("analysis_function_not_allowed", "for 循环目标只能是局部变量")
        if isinstance(node, ast.comprehension) and (
            node.is_async or not _assignment_target_is_allowed(node.target)
        ):
            raise QueryWorkspaceError("analysis_function_not_allowed", "推导式只能同步遍历受限数据")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCTION_CALLS
        ):
            raise QueryWorkspaceError("analysis_function_not_allowed", "函数调用不在受控白名单中")


_FUNCTION_RUNNER = r"""
import ast, json, sys

payload = json.loads(sys.stdin.read())
scope = {"__builtins__": {}, "abs": abs, "enumerate": enumerate, "float": float,
         "int": int, "len": len, "max": max, "min": min, "range": range,
         "round": round, "sorted": sorted, "str": str, "sum": sum}
exec(compile(ast.parse(payload["code"], mode="exec"), "<analysis>", "exec"), scope, scope)
result = scope["analyze"](payload["rows"])
if not isinstance(result, dict):
    raise ValueError("analyze must return object")
print(json.dumps(result, ensure_ascii=False, allow_nan=False, default=str))
"""


def _limit_resources() -> None:
    for limit, values in (
        (resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS + 1)),
        (resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES)),
        (resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES)),
        (resource.RLIMIT_NOFILE, (16, 16)),
    ):
        try:
            resource.setrlimit(limit, values)
        except (OSError, ValueError):
            continue


def _analysis_for_turn(
    root: Path, row: dict[str, Any], turn_id: str, analysis_id: str
) -> dict[str, Any]:
    _turn_for_row(root, row, turn_id)
    with storage.open_db(root) as connection:
        analysis = storage.get_analysis_run(connection, analysis_id)
    if (
        analysis is None
        or analysis["session_id"] != row["id"]
        or analysis.get("turn_id") != turn_id
        or analysis["ticker"] != row["ticker"]
    ):
        raise QueryWorkspaceError("unknown_analysis_run", "分析执行不属于当前 Turn")
    return analysis


def _input_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """只描述实际输入覆盖，不推断单位或自动混合不同科目计算。"""
    series = {}
    for row in rows:
        key = tuple(row.get(field) for field in ("field_key", "label", "unit", "scale", "currency"))
        item = series.setdefault(
            key,
            {
                **dict(zip(("field_key", "label", "unit", "scale", "currency"), key, strict=True)),
                "periods": [],
                "sample_count": 0,
            },
        )
        item["sample_count"] += 1
        if row.get("period") is not None and row["period"] not in item["periods"]:
            item["periods"].append(row["period"])
    return {
        "row_count": len(rows),
        "series": list(series.values())[:20],
        "series_omitted": max(0, len(series) - 20),
        "note": "只记录 SQL 实际选择的输入；缺失字段保持 null，不证明完整历史或统一会计口径。",
    }


def run_analysis_function(
    root: Path, row: dict[str, Any], turn_id: str, analysis_id: str, code: str
) -> dict[str, Any]:
    validate_function(code)
    analysis = _analysis_for_turn(root, row, turn_id, analysis_id)
    if analysis["status"] != "sql_succeeded":
        raise QueryWorkspaceError("analysis_not_ready", "函数只能处理已成功的 SQL 结果")
    session_id = str(row["id"])
    with storage.open_db(root) as connection:
        function_count = sum(
            1
            for item in storage.list_analysis_runs(connection, session_id, turn_id=turn_id)
            if item.get("code_hash")
        )
    if function_count >= MAX_FUNCTION_EXECUTIONS_PER_TURN:
        raise QueryWorkspaceError("analysis_function_limit", "每个 Turn 最多执行三次临时函数")
    workspace = root / str(analysis["workspace_path"])
    sql_result_path = workspace / "sql-result.json"
    if not sql_result_path.is_file():
        raise QueryWorkspaceError("analysis_result_missing", "对应 SQL 结果文件不存在", exit_code=1)
    try:
        sql_result = json.loads(sql_result_path.read_text(encoding="utf-8"))
        rows = sql_result["rows"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise QueryWorkspaceError(
            "analysis_result_invalid", "对应 SQL 结果无法读取", exit_code=1
        ) from error
    if not isinstance(rows, list):
        raise QueryWorkspaceError("analysis_result_invalid", "对应 SQL 结果行格式无效", exit_code=1)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    storage.write_atomic(workspace / "function.py", code)
    (workspace / "function.py").chmod(0o444)
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _FUNCTION_RUNNER],
            input=json.dumps({"code": code, "rows": rows}, ensure_ascii=False),
            text=True,
            capture_output=True,
            cwd=workspace,
            env={"PATH": os.defpath, "LC_ALL": "C"},
            timeout=TIMEOUT_SECONDS,
            preexec_fn=_limit_resources,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        _finish_failure(root, analysis_id, "function_failed", "临时函数超过十秒限制")
        raise QueryWorkspaceError("analysis_function_timeout", "临时函数超过十秒限制") from error
    if completed.returncode != 0:
        _finish_failure(root, analysis_id, "function_failed", "临时函数被限制或执行失败")
        raise QueryWorkspaceError("analysis_function_failed", "临时函数被限制或执行失败")
    if len(completed.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        _finish_failure(root, analysis_id, "function_failed", "临时函数结果超过 64 KB 限制")
        raise QueryWorkspaceError("analysis_output_too_large", "临时函数结果超过 64 KB 限制")
    try:
        function_result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        _finish_failure(root, analysis_id, "function_failed", "临时函数没有返回 JSON")
        raise QueryWorkspaceError("analysis_result_invalid", "临时函数没有返回 JSON") from error
    input_summary = _input_summary(rows)
    combined = {"sql": sql_result, "function": function_result, "input_summary": input_summary}
    rendered = json.dumps(combined, ensure_ascii=False, allow_nan=False, default=str)
    storage.write_atomic(workspace / "function-result.json", rendered + "\n")
    (workspace / "function-result.json").chmod(0o444)
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.finish_analysis_run(
            connection,
            analysis_id,
            status="function_succeeded",
            result_json=rendered,
            result_summary="临时函数已处理 SQL 结果",
            finished_at=storage.now_utc(),
        )
        connection.execute(
            "UPDATE analysis_runs SET code_hash = ? WHERE id = ?", (code_hash, analysis_id)
        )
    return {
        "analysis_id": analysis_id,
        "status": "function_succeeded",
        "result": function_result,
        "input_summary": input_summary,
        "function_hash": code_hash,
        "source_refs": json.loads(str(analysis["source_refs_json"])),
    }


def read_analysis_artifact(
    root: Path, row: dict[str, Any], turn_id: str, analysis_id: str
) -> dict[str, Any]:
    analysis = _analysis_for_turn(root, row, turn_id, analysis_id)
    if analysis["status"] not in {
        "sql_succeeded",
        "function_succeeded",
        "sql_failed",
        "function_failed",
    }:
        raise QueryWorkspaceError("analysis_not_ready", "该分析尚未完成")
    return {
        "analysis_id": analysis_id,
        "status": analysis["status"],
        "dataset_hash": analysis["dataset_hash"],
        "sql_hash": analysis.get("sql_hash"),
        "function_hash": analysis["code_hash"],
        "result": json.loads(analysis["result_json"]) if analysis["result_json"] else None,
        "result_summary": analysis["result_summary"],
        "source_refs": json.loads(str(analysis["source_refs_json"])),
    }


def _candidate_references(result: dict[str, Any]) -> list[dict[str, str | None]]:
    candidates: list[dict[str, str | None]] = []

    def add(value: object, *, fact_id: str | None = None) -> None:
        if not isinstance(value, dict):
            return
        source_id = value.get("source_id")
        locator = value.get("locator")
        if (
            not isinstance(source_id, str)
            or not isinstance(locator, str)
            or not source_id
            or not locator
        ):
            return
        relation = value.get("relation")
        candidates.append(
            {
                "source_id": source_id,
                "locator": locator,
                "relation": relation
                if relation in {"supports", "refutes", "context"}
                else "supports",
                "fact_id": fact_id
                or (value.get("fact_id") if isinstance(value.get("fact_id"), str) else None),
            }
        )

    for item in result.get("citable_refs", []):
        add(item)
    for item in result.get("source_refs", []):
        add(item)
    for item in result.get("records", []):
        add(item)
    for item in result.get("results", []):
        add(item)
    add(result)
    unique: dict[tuple[str, str, str, str | None], dict[str, str | None]] = {}
    for item in candidates:
        key = (str(item["source_id"]), str(item["locator"]), str(item["relation"]), item["fact_id"])
        unique[key] = item
    return list(unique.values())


def record_tool_evidence_receipts(
    root: Path,
    row: dict[str, Any],
    turn_id: str,
    tool_id: str,
    result: dict[str, Any],
) -> int:
    """只为实际返回的 source_id + locator 建立当前 Turn 的可检查回执。"""
    _turn_for_row(root, row, turn_id)
    session_id = str(row["id"])
    analysis_id = result.get("analysis_id") if isinstance(result.get("analysis_id"), str) else None
    inserted = 0
    with storage.project_lock(root), storage.open_db(root) as connection:
        for item in _candidate_references(result):
            source = storage.get_source(connection, str(item["source_id"]))
            if source is None or source["ticker"] != row["ticker"]:
                continue
            scoped_row = {**row, "turn_id": turn_id}
            if not agent_company.allowed(connection, scoped_row, source):
                continue
            if agent_company.scope(connection, scoped_row) is not None:
                agent_company.verify_archive(root, source)
                if item.get("source_sha256") and item["source_sha256"] != source["sha256"]:
                    continue
            storage.insert_agent_evidence_receipt(
                connection,
                receipt_id=str(uuid4()),
                session_id=session_id,
                turn_id=turn_id,
                analysis_id=analysis_id,
                tool_id=tool_id,
                source_id=str(item["source_id"]),
                locator=str(item["locator"]),
                relation=str(item["relation"]),
                source_sha256=str(source["sha256"]),
                fact_id=item["fact_id"],
                created_at=storage.now_utc(),
            )
            inserted += 1
    return inserted
