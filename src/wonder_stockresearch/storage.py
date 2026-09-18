"""阶段 2 的 SQLite 与文件存储；只做持久化，不含研究规则。"""

from __future__ import annotations

import fcntl
import hashlib
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
LOCK_TIMEOUT_SECONDS = 5.0

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS stocks (
        ticker TEXT PRIMARY KEY,
        name TEXT,
        watched INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL REFERENCES stocks(ticker),
        kind TEXT NOT NULL DEFAULT 'standard',
        parent_run_id TEXT REFERENCES runs(id),
        as_of TEXT NOT NULL,
        baseline_run_id TEXT REFERENCES runs(id),
        status TEXT NOT NULL,
        draft_json TEXT,
        schema_version INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        error_json TEXT,
        artifact_hashes_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sources (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL REFERENCES stocks(ticker),
        kind TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        raw_path TEXT NOT NULL,
        text_path TEXT,
        retrieved_at TEXT NOT NULL,
        source_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_sources (
        run_id TEXT NOT NULL REFERENCES runs(id),
        source_id TEXT NOT NULL REFERENCES sources(id),
        PRIMARY KEY (run_id, source_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS runs_ticker_created ON runs(ticker, created_at)",
)


class StorageError(Exception):
    """数据库或文件层错误，带稳定错误码供 CLI 输出。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    """统一以 UTC 存储时间，显示时区交给上层。"""
    return value.astimezone(UTC).isoformat()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def utc_compact(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def db_path(root: Path) -> Path:
    return root / "data" / "wonder.db"


def connect(root: Path) -> sqlite3.Connection:
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def open_db(root: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(root)
    try:
        yield connection
    finally:
        connection.close()


def initialize(root: Path) -> None:
    """按 PRAGMA user_version 建库；已有表不重建。"""
    with open_db(root) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            raise StorageError("schema_version_unsupported", f"未知的数据库版本：{version}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _SCHEMA:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


@contextmanager
def project_lock(root: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """项目内写锁；持锁期间不得进行网络下载。"""
    lock_path = root / "data" / ".write.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StorageError("lock_timeout", "项目写锁在 5 秒内未能取得") from None
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def stock_dir(root: Path, ticker: str) -> Path:
    return root / "data" / "stocks" / ticker


def sources_dir(root: Path, ticker: str) -> Path:
    return stock_dir(root, ticker) / "sources"


def run_dir(root: Path, ticker: str, created_at: datetime, run_id: str) -> Path:
    return stock_dir(root, ticker) / "runs" / f"{utc_compact(created_at)}-{run_id}"


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    """先写临时文件再替换，避免崩溃留下半截产物。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def ensure_stock(connection: sqlite3.Connection, ticker: str, created_at: datetime) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO stocks(ticker, name, watched, created_at) VALUES (?, NULL, 0, ?)",
        (ticker, to_iso(created_at)),
    )


def watch_stock(
    connection: sqlite3.Connection, ticker: str, name: str | None, created_at: datetime
) -> None:
    connection.execute(
        """
        INSERT INTO stocks(ticker, name, watched, created_at) VALUES (?, ?, 1, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            watched = 1,
            name = COALESCE(excluded.name, stocks.name)
        """,
        (ticker, name, to_iso(created_at)),
    )


def unwatch_stock(connection: sqlite3.Connection, ticker: str) -> bool:
    cursor = connection.execute("UPDATE stocks SET watched = 0 WHERE ticker = ?", (ticker,))
    return cursor.rowcount > 0


def list_stocks(connection: sqlite3.Connection, watched_only: bool) -> list[dict[str, Any]]:
    query = "SELECT ticker, name, watched, created_at FROM stocks"
    if watched_only:
        query += " WHERE watched = 1"
    query += " ORDER BY ticker"
    return [dict(row) for row in connection.execute(query)]


def insert_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    ticker: str,
    as_of: datetime,
    kind: str,
    parent_run_id: str | None,
    baseline_run_id: str | None,
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO runs(
            id, ticker, kind, parent_run_id, as_of, baseline_run_id, status,
            draft_json, schema_version, created_at, updated_at,
            completed_at, error_json, artifact_hashes_json
        ) VALUES (?, ?, ?, ?, ?, ?, 'draft', NULL, ?, ?, ?, NULL, NULL, NULL)
        """,
        (
            run_id,
            ticker,
            kind,
            parent_run_id,
            to_iso(as_of),
            baseline_run_id,
            SCHEMA_VERSION,
            to_iso(created_at),
            to_iso(created_at),
        ),
    )


def get_run(connection: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return row_to_dict(row)


def list_runs(connection: sqlite3.Connection, ticker: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, ticker, kind, parent_run_id, as_of, baseline_run_id, status,
               created_at, completed_at, error_json
        FROM runs WHERE ticker = ? ORDER BY created_at DESC, id DESC
        """,
        (ticker,),
    ).fetchall()
    return [dict(row) for row in rows]


def update_draft(connection: sqlite3.Connection, run_id: str, draft_json: str, updated_at: datetime) -> None:
    connection.execute(
        "UPDATE runs SET draft_json = ?, updated_at = ? WHERE id = ?",
        (draft_json, to_iso(updated_at), run_id),
    )


def mark_run_failed(
    connection: sqlite3.Connection, run_id: str, error_json: str, updated_at: datetime
) -> None:
    connection.execute(
        "UPDATE runs SET status = 'failed', error_json = ?, updated_at = ? WHERE id = ? AND status = 'draft'",
        (error_json, to_iso(updated_at), run_id),
    )


def mark_run_completed(
    connection: sqlite3.Connection,
    run_id: str,
    completed_at: datetime,
    artifact_hashes_json: str,
) -> int:
    cursor = connection.execute(
        """
        UPDATE runs
        SET status = 'completed', completed_at = ?, updated_at = ?, error_json = NULL,
            artifact_hashes_json = ?
        WHERE id = ? AND status = 'draft'
        """,
        (to_iso(completed_at), to_iso(completed_at), artifact_hashes_json, run_id),
    )
    return cursor.rowcount


def mark_run_resumed(connection: sqlite3.Connection, run_id: str, updated_at: datetime) -> int:
    cursor = connection.execute(
        "UPDATE runs SET status = 'draft', error_json = NULL, updated_at = ? WHERE id = ? AND status = 'failed'",
        (to_iso(updated_at), run_id),
    )
    return cursor.rowcount


def upsert_source(connection: sqlite3.Connection, source_row: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO sources(id, ticker, kind, metadata_json, sha256, raw_path, text_path,
                            retrieved_at, source_json)
        VALUES (:id, :ticker, :kind, :metadata_json, :sha256, :raw_path, :text_path,
                :retrieved_at, :source_json)
        ON CONFLICT(id) DO UPDATE SET
            kind = excluded.kind,
            metadata_json = excluded.metadata_json,
            sha256 = excluded.sha256,
            raw_path = excluded.raw_path,
            text_path = excluded.text_path,
            retrieved_at = excluded.retrieved_at,
            source_json = excluded.source_json
        """,
        source_row,
    )


def link_run_source(connection: sqlite3.Connection, run_id: str, source_id: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO run_sources(run_id, source_id) VALUES (?, ?)",
        (run_id, source_id),
    )


def get_run_sources(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT sources.* FROM sources
        JOIN run_sources ON run_sources.source_id = sources.id
        WHERE run_sources.run_id = ?
        ORDER BY sources.id
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]
