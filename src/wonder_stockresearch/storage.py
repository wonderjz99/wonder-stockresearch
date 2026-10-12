"""阶段 2 的 SQLite 与文件存储；只做持久化，不含研究规则。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 14
LOCK_TIMEOUT_SECONDS = 5.0
STEP_STATUSES = ("pending", "running", "succeeded", "failed", "blocked", "skipped")

_RUN_STEPS_DDL_V2 = """
    CREATE TABLE IF NOT EXISTS run_steps (
        run_id TEXT NOT NULL REFERENCES runs(id),
        step_key TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        status TEXT NOT NULL,
        input_hash TEXT,
        output_ref TEXT,
        provider TEXT,
        model TEXT,
        started_at TEXT,
        finished_at TEXT,
        error_json TEXT,
        PRIMARY KEY (run_id, step_key, attempt)
    )
"""

# 阶段 7 增加受控运行元数据列（模板版本、用量摘要等），不保存提示词、认证头或隐藏推理。
_RUN_STEPS_METADATA_DDL = "ALTER TABLE run_steps ADD COLUMN metadata_json TEXT"

_RUN_STEPS_DDL = """
    CREATE TABLE IF NOT EXISTS run_steps (
        run_id TEXT NOT NULL REFERENCES runs(id),
        step_key TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        status TEXT NOT NULL,
        input_hash TEXT,
        output_ref TEXT,
        provider TEXT,
        model TEXT,
        started_at TEXT,
        finished_at TEXT,
        error_json TEXT,
        metadata_json TEXT,
        PRIMARY KEY (run_id, step_key, attempt)
    )
"""

_AGENT_DDL = (
    """
    CREATE TABLE IF NOT EXISTS agent_sessions (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL REFERENCES stocks(ticker),
        run_id TEXT REFERENCES runs(id),
        question_json TEXT NOT NULL,
        status TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        max_iterations INTEGER NOT NULL,
        iteration INTEGER NOT NULL DEFAULT 0,
        token_budget INTEGER NOT NULL,
        used_tokens INTEGER NOT NULL DEFAULT 0,
        result_json TEXT,
        error_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS agent_sessions_ticker_created
        ON agent_sessions(ticker, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_events (
        session_id TEXT NOT NULL REFERENCES agent_sessions(id),
        sequence INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(session_id, sequence)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS interactive_reports (
        session_id TEXT PRIMARY KEY REFERENCES agent_sessions(id),
        ticker TEXT NOT NULL REFERENCES stocks(ticker),
        report_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS company_memos (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL REFERENCES stocks(ticker),
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        source_refs_json TEXT NOT NULL,
        created_from_session TEXT NOT NULL REFERENCES agent_sessions(id),
        tags_json TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS company_memos_ticker_created
        ON company_memos(ticker, created_at)
    """,
)

_CHAT_DDL = (
    """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        ticker TEXT,
        run_id TEXT REFERENCES runs(id),
        last_agent_session_id TEXT REFERENCES agent_sessions(id),
        pending_json TEXT,
        history_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS chat_sessions_updated
        ON chat_sessions(updated_at)
    """,
)

_DRAFT_QUALITY_DDL = (
    """
    CREATE TABLE IF NOT EXISTS draft_sections (
        run_id TEXT NOT NULL REFERENCES runs(id),
        section_key TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        output_json TEXT NOT NULL,
        output_hash TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        template_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(run_id, section_key, input_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS draft_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        draft_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        reviewer_type TEXT NOT NULL,
        provider TEXT,
        model TEXT,
        template_version TEXT NOT NULL,
        review_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS draft_reviews_run_created ON draft_reviews(run_id, id)",
)

# 阶段 16.1：页级来源索引只保存已归档文本的可复现切片。它不是向量库，
# 也不保存模型提示词、回答或隐藏推理。source_id 对应现有 sources 表；虚拟表
# 不能声明外键，因此删除来源时须由调用方先清理索引（当前来源永不物理删除）。
_SOURCE_PAGE_FTS_DDL = """
    CREATE VIRTUAL TABLE IF NOT EXISTS source_page_fts USING fts5(
        source_id UNINDEXED,
        locator UNINDEXED,
        content,
        content_hash UNINDEXED,
        tokenize = 'unicode61'
    )
"""

# 阶段 18：交互 Agent 的工作记忆、可检查长期记忆、临时分析审计，以及来源数值核对。
# 这些记录不保存模型提示词、隐藏推理、认证信息或完整工具结果。
_AGENT_MEMORY_DDL = (
    """
    CREATE TABLE IF NOT EXISTS agent_scratchpads (
        session_id TEXT PRIMARY KEY REFERENCES agent_sessions(id),
        revision INTEGER NOT NULL,
        objective TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        observations_json TEXT NOT NULL,
        calculation_refs_json TEXT NOT NULL,
        source_refs_json TEXT NOT NULL,
        open_questions_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        compression_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_entries (
        id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL REFERENCES stocks(ticker),
        memory_type TEXT NOT NULL,
        status TEXT NOT NULL,
        topic TEXT NOT NULL,
        as_of TEXT,
        content TEXT NOT NULL,
        source_refs_json TEXT NOT NULL,
        origin_ref TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS memory_entries_ticker_created
        ON memory_entries(ticker, created_at DESC)
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_entry_fts USING fts5(
        entry_id UNINDEXED,
        ticker UNINDEXED,
        topic,
        content,
        tokenize = 'unicode61'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_runs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES agent_sessions(id),
        ticker TEXT NOT NULL REFERENCES stocks(ticker),
        execution_index INTEGER NOT NULL,
        status TEXT NOT NULL,
        dataset_hash TEXT NOT NULL,
        code_hash TEXT,
        limits_json TEXT NOT NULL,
        result_json TEXT,
        result_summary TEXT,
        source_refs_json TEXT NOT NULL,
        workspace_path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        finished_at TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS analysis_runs_session_created
        ON analysis_runs(session_id, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS source_conflicts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        provider_source_id TEXT NOT NULL REFERENCES sources(id),
        official_source_id TEXT REFERENCES sources(id),
        metric TEXT NOT NULL,
        period TEXT,
        currency TEXT,
        provider_value TEXT,
        official_value TEXT,
        status TEXT NOT NULL,
        details_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS source_conflicts_run_status
        ON source_conflicts(run_id, status)
    """,
)

# 阶段 19：调用轨迹只保存结构化元数据、时序和安全摘要。它刻意不保存 API key、
# Authorization、完整 prompt / 模型输出、工具完整结果或隐藏推理。
_TRACE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS traces (
        id TEXT PRIMARY KEY,
        trace_kind TEXT NOT NULL,
        name TEXT NOT NULL,
        run_id TEXT,
        session_id TEXT,
        attributes_json TEXT NOT NULL,
        status TEXT NOT NULL,
        error_json TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS traces_run_started ON traces(run_id, started_at)",
    "CREATE INDEX IF NOT EXISTS traces_session_started ON traces(session_id, started_at)",
    """
    CREATE TABLE IF NOT EXISTS trace_spans (
        id TEXT PRIMARY KEY,
        trace_id TEXT NOT NULL REFERENCES traces(id),
        parent_span_id TEXT,
        span_kind TEXT NOT NULL,
        name TEXT NOT NULL,
        attributes_json TEXT NOT NULL,
        status TEXT NOT NULL,
        error_json TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS trace_spans_trace_started ON trace_spans(trace_id, started_at)",
)

# 阶段 19C：QueryWorkspace 的最小 Turn 边界与分析证据回执。Turn 只固定一次
# 用户问题和授权/预算快照；Attempt、恢复和报告历史仍由阶段 19D 处理。
_QUERY_WORKSPACE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS agent_turns (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES agent_sessions(id),
        ticker TEXT NOT NULL REFERENCES stocks(ticker),
        run_id TEXT REFERENCES runs(id),
        question_json TEXT NOT NULL,
        skill_audit_json TEXT NOT NULL,
        allowed_tools_json TEXT,
        tool_registry_version TEXT,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        budget_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS agent_turns_session_created
        ON agent_turns(session_id, created_at)
    """,
    "ALTER TABLE analysis_runs ADD COLUMN turn_id TEXT REFERENCES agent_turns(id)",
    "ALTER TABLE analysis_runs ADD COLUMN sql_hash TEXT",
    """
    CREATE INDEX IF NOT EXISTS analysis_runs_turn_created
        ON analysis_runs(turn_id, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_evidence_receipts (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES agent_sessions(id),
        turn_id TEXT NOT NULL REFERENCES agent_turns(id),
        analysis_id TEXT REFERENCES analysis_runs(id),
        tool_id TEXT NOT NULL,
        source_id TEXT NOT NULL REFERENCES sources(id),
        locator TEXT NOT NULL,
        relation TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        fact_id TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS agent_evidence_receipts_turn_source
        ON agent_evidence_receipts(turn_id, source_id, locator)
    """,
    """
    INSERT OR IGNORE INTO agent_turns(
        id, session_id, ticker, run_id, question_json, skill_audit_json,
        allowed_tools_json, tool_registry_version, provider, model, budget_json, created_at
    )
    SELECT
        'legacy-' || id, id, ticker, run_id, question_json, '[]', NULL, NULL,
        provider, model,
        '{"max_iterations":' || max_iterations || ',"token_budget":' || token_budget || '}',
        created_at
    FROM agent_sessions
    """,
    """
    UPDATE analysis_runs
    SET turn_id = 'legacy-' || session_id
    WHERE turn_id IS NULL
    """,
)

# 阶段 19D：把一次用户目标（Turn）与一次实际执行（Attempt）分开。Turn 是不可变
# 问题、授权和总预算的所有者；Attempt 只是一次可停止、可重试的执行记录。它不是通用
# 事件溯源或后台任务系统。
_AGENT_RUNTIME_COMPAT_DDL = (
    # 正常 v10 数据库已包含这些表。显式补齐只存在核心 19C 表的教学/早期数据库，
    # 使 v11 迁移可恢复，而不会在 ALTER TABLE 时留下半迁移状态。
    """
    CREATE TABLE IF NOT EXISTS agent_events (
        session_id TEXT NOT NULL REFERENCES agent_sessions(id), sequence INTEGER NOT NULL,
        event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(session_id, sequence)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS traces (
        id TEXT PRIMARY KEY, trace_kind TEXT NOT NULL, name TEXT NOT NULL, run_id TEXT,
        session_id TEXT, attributes_json TEXT NOT NULL, status TEXT NOT NULL,
        error_json TEXT, started_at TEXT NOT NULL, finished_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_memos (
        id TEXT PRIMARY KEY, ticker TEXT NOT NULL REFERENCES stocks(ticker), title TEXT NOT NULL,
        content TEXT NOT NULL, source_refs_json TEXT NOT NULL,
        created_from_session TEXT NOT NULL REFERENCES agent_sessions(id), tags_json TEXT NOT NULL,
        version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS interactive_reports (
        session_id TEXT PRIMARY KEY REFERENCES agent_sessions(id),
        ticker TEXT NOT NULL REFERENCES stocks(ticker), report_json TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )
    """,
)

_AGENT_RUNTIME_DDL = (
    *_AGENT_RUNTIME_COMPAT_DDL,
    "ALTER TABLE agent_turns ADD COLUMN status TEXT NOT NULL DEFAULT 'created'",
    "ALTER TABLE agent_turns ADD COLUMN iteration INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agent_turns ADD COLUMN used_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agent_turns ADD COLUMN error_json TEXT",
    "ALTER TABLE agent_turns ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    "UPDATE agent_turns SET updated_at = created_at WHERE updated_at = ''",
    """
    CREATE TABLE IF NOT EXISTS agent_attempts (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES agent_sessions(id),
        turn_id TEXT NOT NULL REFERENCES agent_turns(id),
        attempt_index INTEGER NOT NULL,
        status TEXT NOT NULL,
        input_json TEXT NOT NULL,
        iteration INTEGER NOT NULL DEFAULT 0,
        used_tokens INTEGER NOT NULL DEFAULT 0,
        error_json TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        UNIQUE(turn_id, attempt_index)
    )
    """,
    "CREATE INDEX IF NOT EXISTS agent_attempts_turn_started ON agent_attempts(turn_id, started_at)",
    "ALTER TABLE agent_events ADD COLUMN turn_id TEXT REFERENCES agent_turns(id)",
    "ALTER TABLE agent_events ADD COLUMN attempt_id TEXT REFERENCES agent_attempts(id)",
    "CREATE INDEX IF NOT EXISTS agent_events_turn_sequence ON agent_events(turn_id, sequence)",
    "ALTER TABLE traces ADD COLUMN turn_id TEXT REFERENCES agent_turns(id)",
    "ALTER TABLE traces ADD COLUMN attempt_id TEXT REFERENCES agent_attempts(id)",
    "CREATE INDEX IF NOT EXISTS traces_turn_started ON traces(turn_id, started_at)",
    "ALTER TABLE company_memos ADD COLUMN created_from_turn TEXT REFERENCES agent_turns(id)",
    "ALTER TABLE company_memos ADD COLUMN created_from_report_turn TEXT REFERENCES agent_turns(id)",
    """
    UPDATE company_memos
    SET created_from_turn = (
        SELECT id FROM agent_turns
        WHERE agent_turns.session_id = company_memos.created_from_session
        ORDER BY created_at DESC, id DESC LIMIT 1
    )
    WHERE created_from_turn IS NULL
    """,
    """
    ALTER TABLE interactive_reports RENAME TO legacy_interactive_reports
    """,
    """
    CREATE TABLE interactive_reports (
        turn_id TEXT PRIMARY KEY REFERENCES agent_turns(id),
        session_id TEXT NOT NULL REFERENCES agent_sessions(id),
        attempt_id TEXT REFERENCES agent_attempts(id),
        ticker TEXT NOT NULL REFERENCES stocks(ticker),
        report_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS interactive_reports_session_created ON interactive_reports(session_id, created_at)",
    """
    INSERT OR IGNORE INTO interactive_reports(
        turn_id, session_id, attempt_id, ticker, report_json, created_at
    )
    SELECT
        (
            SELECT id FROM agent_turns
            WHERE agent_turns.session_id = legacy_interactive_reports.session_id
            ORDER BY created_at DESC, id DESC LIMIT 1
        ),
        session_id, NULL, ticker, report_json, created_at
    FROM legacy_interactive_reports
    WHERE EXISTS(
        SELECT 1 FROM agent_turns
        WHERE agent_turns.session_id = legacy_interactive_reports.session_id
    )
    """,
    """
    UPDATE agent_turns
    SET status = 'legacy'
    WHERE status = 'created'
      AND id LIKE 'legacy-%'
    """,
)

_AGENT_RECOVERY_DDL = (
    # v11 已可能被使用，因此修复作为独立迁移，不悄悄改写旧迁移。
    """
    UPDATE agent_turns
    SET iteration = (SELECT iteration FROM agent_sessions WHERE id = agent_turns.session_id),
        used_tokens = (SELECT used_tokens FROM agent_sessions WHERE id = agent_turns.session_id),
        status = CASE (SELECT status FROM agent_sessions WHERE id = agent_turns.session_id)
            WHEN 'completed' THEN 'succeeded'
            WHEN 'waiting_user' THEN 'waiting_user'
            WHEN 'failed' THEN 'failed'
            WHEN 'budget_exhausted' THEN 'budget_exhausted'
            ELSE 'interrupted' END
    WHERE status = 'legacy' AND id LIKE 'legacy-%'
      AND NOT EXISTS (SELECT 1 FROM agent_attempts WHERE turn_id = agent_turns.id)
    """,
    """
    UPDATE agent_events SET turn_id = 'legacy-' || session_id
    WHERE turn_id IS NULL AND EXISTS (
        SELECT 1 FROM agent_turns WHERE id = 'legacy-' || agent_events.session_id
    )
    """,
    """
    UPDATE traces SET turn_id = 'legacy-' || session_id
    WHERE turn_id IS NULL AND EXISTS (
        SELECT 1 FROM agent_turns WHERE id = 'legacy-' || traces.session_id
    )
    """,
)

_PI_MESSAGES_DDL = (
    """CREATE TABLE agent_messages (
        turn_id TEXT NOT NULL REFERENCES agent_turns(id),
        sequence INTEGER NOT NULL,
        session_id TEXT NOT NULL REFERENCES agent_sessions(id),
        attempt_id TEXT NOT NULL REFERENCES agent_attempts(id),
        role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'toolResult')),
        message_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(turn_id, sequence)
    )""",
    "CREATE INDEX agent_messages_session_turn ON agent_messages(session_id, turn_id, sequence)",
)

_COMPANY_NOTEBOOK_DDL = (
    """CREATE TABLE company_notebooks (
        ticker TEXT PRIMARY KEY REFERENCES stocks(ticker),
        id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE company_memo_links (
        memo_id TEXT PRIMARY KEY REFERENCES company_memos(id),
        ticker TEXT NOT NULL REFERENCES company_notebooks(ticker),
        report_turn_id TEXT UNIQUE REFERENCES agent_turns(id),
        revision INTEGER NOT NULL CHECK(revision > 0),
        UNIQUE(ticker, revision)
    )""",
    """INSERT INTO company_notebooks(ticker,id,title,created_at,updated_at)
    SELECT ticker,
        lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' ||
        substr(lower(hex(randomblob(2))),2) || '-a' ||
        substr(lower(hex(randomblob(2))),2) || '-' || lower(hex(randomblob(6))),
        ticker || ' 公司备忘录', MIN(created_at), MAX(updated_at)
    FROM company_memos GROUP BY ticker""",
    """INSERT INTO company_memo_links(memo_id,ticker,report_turn_id,revision)
    SELECT id,ticker,NULL,ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY created_at,id)
    FROM company_memos""",
)

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
    _RUN_STEPS_DDL,
    *_AGENT_DDL,
    *_CHAT_DDL,
    *_DRAFT_QUALITY_DDL,
    _SOURCE_PAGE_FTS_DDL,
    *_AGENT_MEMORY_DDL,
    *_TRACE_DDL,
    *_QUERY_WORKSPACE_DDL,
    *_AGENT_RUNTIME_DDL,
    *_AGENT_RECOVERY_DDL,
    *_PI_MESSAGES_DDL,
    *_COMPANY_NOTEBOOK_DDL,
)

# 显式递增迁移：键为目标版本，值为从上一版升级到该版需要执行的语句。
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (_RUN_STEPS_DDL_V2,),
    3: (_RUN_STEPS_METADATA_DDL,),
    4: _AGENT_DDL,
    5: _CHAT_DDL,
    6: _DRAFT_QUALITY_DDL,
    7: (_SOURCE_PAGE_FTS_DDL,),
    8: _AGENT_MEMORY_DDL,
    9: _TRACE_DDL,
    10: _QUERY_WORKSPACE_DDL,
    11: _AGENT_RUNTIME_DDL,
    12: _AGENT_RECOVERY_DDL,
    13: _PI_MESSAGES_DDL,
    14: _COMPANY_NOTEBOOK_DDL,
}


def append_agent_message(
    connection: sqlite3.Connection, *, session_id: str, turn_id: str,
    attempt_id: str, message: dict[str, Any],
) -> None:
    """调用方先投影/过滤内容；这里只保存有序的规范化消息，不保存 provider 原响应。"""
    owner = connection.execute(
        "SELECT 1 FROM agent_attempts WHERE id=? AND turn_id=? AND session_id=?",
        (attempt_id, turn_id, session_id),
    ).fetchone()
    if owner is None:
        raise StorageError("message_owner_mismatch", "消息不属于当前 Turn/Attempt")
    sequence = connection.execute(
        "SELECT COALESCE(MAX(sequence),0)+1 FROM agent_messages WHERE turn_id=?", (turn_id,),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO agent_messages VALUES (?,?,?,?,?,?,?)",
        (turn_id, sequence, session_id, attempt_id, message["role"],
         json.dumps(message, ensure_ascii=False, allow_nan=False), to_iso(now_utc())),
    )


def list_agent_messages(connection: sqlite3.Connection, turn_id: str) -> list[dict[str, Any]]:
    return [json.loads(row[0]) for row in connection.execute(
        "SELECT message_json FROM agent_messages WHERE turn_id=? ORDER BY sequence", (turn_id,),
    )]


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
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """将多条 SQL 作为一次提交；嵌套调用只使用 savepoint。"""
    from uuid import uuid4

    nested = connection.in_transaction
    savepoint = f"wonder_{uuid4().hex}"
    connection.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if nested:
            connection.execute(f"ROLLBACK TO {savepoint}")
            connection.execute(f"RELEASE {savepoint}")
        else:
            connection.execute("ROLLBACK")
        raise
    else:
        connection.execute(f"RELEASE {savepoint}" if nested else "COMMIT")


@contextmanager
def open_db(root: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(root)
    try:
        yield connection
    finally:
        connection.close()


def initialize(root: Path) -> None:
    """按 PRAGMA user_version 建库或执行显式迁移；不重建已有表。"""
    with open_db(root) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version > SCHEMA_VERSION:
            raise StorageError("schema_version_unsupported", f"数据库版本高于本程序：{version}")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if version == 0:
                for statement in _SCHEMA:
                    connection.execute(statement)
            else:
                for target in range(version + 1, SCHEMA_VERSION + 1):
                    for statement in _MIGRATIONS[target]:
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


@contextmanager
def agent_execution_lock(root: Path, session_id: str) -> Iterator[None]:
    """进程存活期间保护同一会话；崩溃后 OS 自动释放，不占项目写锁。"""
    import fcntl

    key = hashlib.sha256(session_id.encode()).hexdigest()
    path = root / "data" / "agent-locks" / f"{key}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StorageError("agent_busy", "该会话仍有执行进程，不能恢复或重复运行") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
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


def list_sources_for_ticker(connection: sqlite3.Connection, ticker: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM sources WHERE ticker = ? ORDER BY retrieved_at DESC, id DESC",
        (ticker,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_source(connection: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return row_to_dict(row)


def is_agent_turn_source(
    source: dict[str, Any], session_id: str, turn_id: str | None,
) -> bool:
    metadata = json.loads(source["metadata_json"])
    return bool(turn_id and metadata.get("agent_session_id") == session_id
                and metadata.get("agent_turn_id") == turn_id)


def agent_context_sources(
    connection: sqlite3.Connection, ticker: str, run_id: str | None,
    session_id: str, turn_id: str | None,
) -> list[dict[str, Any]]:
    """固定 run 的来源加当前 Turn 补取资料；不把其他 Turn 的快照隐式混入。"""
    base = get_run_sources(connection, run_id) if run_id else list_sources_for_ticker(connection, ticker)
    scoped = connection.execute(
        "SELECT * FROM sources WHERE ticker = ? AND "
        "json_extract(metadata_json, '$.agent_session_id') = ? AND "
        "json_extract(metadata_json, '$.agent_turn_id') = ? ORDER BY id",
        (ticker, session_id, turn_id),
    ).fetchall() if turn_id else []
    rows = {item["id"]: item for item in [*base, *(dict(item) for item in scoped)]
            if not json.loads(item["metadata_json"]).get("agent_turn_id")
            or is_agent_turn_source(item, session_id, turn_id)}
    return list(rows.values())


def find_source_by_hash(
    connection: sqlite3.Connection, ticker: str, sha256: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM sources WHERE ticker = ? AND sha256 = ? ORDER BY retrieved_at LIMIT 1",
        (ticker, sha256),
    ).fetchone()
    return row_to_dict(row)


def is_source_linked(connection: sqlite3.Connection, run_id: str, source_id: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM run_sources WHERE run_id = ? AND source_id = ?",
        (run_id, source_id),
    ).fetchone()
    return row is not None


def replace_source_page_index(
    connection: sqlite3.Connection,
    source_id: str,
    pages: list[dict[str, str]],
) -> None:
    """用同一份归档文本的页/行切片替换索引，避免混入旧提取结果。"""
    connection.execute("DELETE FROM source_page_fts WHERE source_id = ?", (source_id,))
    connection.executemany(
        """INSERT INTO source_page_fts(source_id, locator, content, content_hash)
           VALUES (:source_id, :locator, :content, :content_hash)""",
        [{"source_id": source_id, **page} for page in pages],
    )


def indexed_source_page_hashes(connection: sqlite3.Connection, source_id: str) -> list[str]:
    rows = connection.execute(
        """SELECT content_hash FROM source_page_fts
           WHERE source_id = ? ORDER BY locator""",
        (source_id,),
    ).fetchall()
    return [str(row["content_hash"]) for row in rows]


def source_page_rows(
    connection: sqlite3.Connection, source_ids: list[str]
) -> list[dict[str, str]]:
    """按来源与 locator 稳定排序读取索引内容，用于 FTS 无命中时的确定性回退。"""
    if not source_ids:
        return []
    placeholders = ", ".join("?" for _ in source_ids)
    rows = connection.execute(
        f"""SELECT source_id, locator, content, content_hash FROM source_page_fts
             WHERE source_id IN ({placeholders})
             ORDER BY source_id, locator""",
        source_ids,
    ).fetchall()
    return [dict(row) for row in rows]


def search_source_pages(
    connection: sqlite3.Connection,
    source_ids: list[str],
    query: str,
    *,
    limit: int,
) -> list[dict[str, str]]:
    """在指定已关联来源内执行 FTS5 检索；query 为空时返回空而非全表扫描。"""
    if not source_ids or not query.strip() or limit < 1:
        return []
    placeholders = ", ".join("?" for _ in source_ids)
    try:
        rows = connection.execute(
            f"""SELECT source_id, locator, content, content_hash
                 FROM source_page_fts
                 WHERE source_page_fts MATCH ? AND source_id IN ({placeholders})
                 ORDER BY bm25(source_page_fts), source_id, locator
                 LIMIT ?""",
            (query, *source_ids, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # 查询词包含当前 SQLite FTS 版本不接受的字符时，上层仍会使用
        # 同一套关键词评分回退；不把检索语法差异伪装成资料缺口。
        return []
    return [dict(row) for row in rows]


def next_step_attempt(connection: sqlite3.Connection, run_id: str, step_key: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(attempt), 0) FROM run_steps WHERE run_id = ? AND step_key = ?",
        (run_id, step_key),
    ).fetchone()
    return int(row[0]) + 1


def insert_step(
    connection: sqlite3.Connection,
    run_id: str,
    step_key: str,
    attempt: int,
    *,
    status: str,
    provider: str | None = None,
    input_hash: str | None = None,
    output_ref: str | None = None,
    error_json: str | None = None,
    model: str | None = None,
    metadata_json: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO run_steps(run_id, step_key, attempt, status, input_hash, output_ref,
                              provider, model, started_at, finished_at, error_json, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            step_key,
            attempt,
            status,
            input_hash,
            output_ref,
            provider,
            model,
            to_iso(started_at) if started_at else None,
            to_iso(finished_at) if finished_at else None,
            error_json,
            metadata_json,
        ),
    )


def finish_step(
    connection: sqlite3.Connection,
    run_id: str,
    step_key: str,
    attempt: int,
    *,
    status: str,
    output_ref: str | None = None,
    error_json: str | None = None,
    metadata_json: str | None = None,
    finished_at: datetime | None = None,
) -> int:
    cursor = connection.execute(
        """
        UPDATE run_steps
        SET status = ?, output_ref = ?, error_json = ?, metadata_json = ?, finished_at = ?
        WHERE run_id = ? AND step_key = ? AND attempt = ?
        """,
        (
            status,
            output_ref,
            error_json,
            metadata_json,
            to_iso(finished_at or now_utc()),
            run_id,
            step_key,
            attempt,
        ),
    )
    return cursor.rowcount


def list_steps(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT step_key, attempt, status, input_hash, output_ref, provider, model,
               started_at, finished_at, error_json, metadata_json
        FROM run_steps WHERE run_id = ? ORDER BY step_key, attempt
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_draft_section(
    connection: sqlite3.Connection, run_id: str, section_key: str, input_hash: str
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT * FROM draft_sections
           WHERE run_id = ? AND section_key = ? AND input_hash = ?""",
        (run_id, section_key, input_hash),
    ).fetchone()
    return row_to_dict(row)


def save_draft_section(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    section_key: str,
    input_hash: str,
    output_json: str,
    output_hash: str,
    provider: str,
    model: str,
    template_version: str,
    created_at: datetime,
) -> None:
    connection.execute(
        """INSERT OR REPLACE INTO draft_sections(
               run_id, section_key, input_hash, output_json, output_hash,
               provider, model, template_version, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, section_key, input_hash, output_json, output_hash,
         provider, model, template_version, to_iso(created_at)),
    )


def list_draft_sections(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM draft_sections WHERE run_id = ? ORDER BY section_key, created_at",
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_draft_review(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    draft_hash: str,
    status: str,
    reviewer_type: str,
    provider: str | None,
    model: str | None,
    template_version: str,
    review_json: str,
    created_at: datetime,
) -> None:
    connection.execute(
        """INSERT INTO draft_reviews(
               run_id, draft_hash, status, reviewer_type, provider, model,
               template_version, review_json, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, draft_hash, status, reviewer_type, provider, model,
         template_version, review_json, to_iso(created_at)),
    )


def latest_draft_review(connection: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM draft_reviews WHERE run_id = ? ORDER BY id DESC LIMIT 1", (run_id,)
    ).fetchone()
    return row_to_dict(row)


def list_all_sources(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT * FROM sources ORDER BY ticker, id").fetchall()
    return [dict(row) for row in rows]


def list_completed_runs(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM runs WHERE status = 'completed'
        ORDER BY ticker, created_at, id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def latest_run(
    connection: sqlite3.Connection, ticker: str, *, completed_only: bool = False
) -> dict[str, Any] | None:
    query = "SELECT * FROM runs WHERE ticker = ?"
    parameters: tuple[object, ...] = (ticker,)
    if completed_only:
        query += " AND status = 'completed'"
    query += " ORDER BY created_at DESC, id DESC LIMIT 1"
    row = connection.execute(query, parameters).fetchone()
    return row_to_dict(row)


def insert_agent_session(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    ticker: str,
    run_id: str | None,
    question_json: str,
    status: str,
    provider: str,
    model: str,
    max_iterations: int,
    token_budget: int,
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO agent_sessions(
            id, ticker, run_id, question_json, status, provider, model,
            max_iterations, iteration, token_budget, used_tokens,
            result_json, error_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, NULL, NULL, ?, ?)
        """,
        (
            session_id,
            ticker,
            run_id,
            question_json,
            status,
            provider,
            model,
            max_iterations,
            token_budget,
            to_iso(created_at),
            to_iso(created_at),
        ),
    )


def get_agent_session(connection: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,)).fetchone()
    return row_to_dict(row)


def insert_agent_turn(
    connection: sqlite3.Connection,
    *,
    turn_id: str,
    session_id: str,
    ticker: str,
    run_id: str | None,
    question_json: str,
    skill_audit_json: str,
    allowed_tools_json: str | None,
    tool_registry_version: str | None,
    provider: str,
    model: str,
    budget_json: str,
    created_at: datetime,
) -> None:
    """保存一轮用户目标的不可变输入/授权快照，不承担 Attempt 状态机。"""
    connection.execute(
        """
        INSERT INTO agent_turns(
            id, session_id, ticker, run_id, question_json, skill_audit_json,
            allowed_tools_json, tool_registry_version, provider, model, budget_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            turn_id,
            session_id,
            ticker,
            run_id,
            question_json,
            skill_audit_json,
            allowed_tools_json,
            tool_registry_version,
            provider,
            model,
            budget_json,
            to_iso(created_at),
        ),
    )


def get_agent_turn(connection: sqlite3.Connection, turn_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM agent_turns WHERE id = ?", (turn_id,)).fetchone()
    return row_to_dict(row)


def list_agent_turns(connection: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM agent_turns WHERE session_id = ? ORDER BY created_at, id", (session_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def update_agent_turn(
    connection: sqlite3.Connection,
    turn_id: str,
    *,
    status: str,
    iteration: int | None = None,
    used_tokens: int | None = None,
    error_json: str | None = None,
    clear_error: bool = False,
    updated_at: datetime,
) -> int:
    """更新 Turn 的运行状态和累计预算，不修改不可变问题或授权快照。"""
    fields = ["status = ?", "updated_at = ?"]
    values: list[object] = [status, to_iso(updated_at)]
    if iteration is not None:
        fields.append("iteration = ?")
        values.append(iteration)
    if used_tokens is not None:
        fields.append("used_tokens = ?")
        values.append(used_tokens)
    if error_json is not None:
        fields.append("error_json = ?")
        values.append(error_json)
    if clear_error:
        fields.append("error_json = NULL")
    values.append(turn_id)
    cursor = connection.execute(
        f"UPDATE agent_turns SET {', '.join(fields)} WHERE id = ?", values
    )
    return cursor.rowcount


def next_agent_attempt_index(connection: sqlite3.Connection, turn_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(attempt_index), 0) FROM agent_attempts WHERE turn_id = ?", (turn_id,)
    ).fetchone()
    return int(row[0]) + 1


def insert_agent_attempt(
    connection: sqlite3.Connection,
    *,
    attempt_id: str,
    session_id: str,
    turn_id: str,
    attempt_index: int,
    status: str,
    input_json: str,
    started_at: datetime,
) -> None:
    """记录一次实际执行；输入只保存用户补充的结构化 JSON，不保存 prompt。"""
    connection.execute(
        """
        INSERT INTO agent_attempts(
            id, session_id, turn_id, attempt_index, status, input_json,
            iteration, used_tokens, error_json, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, NULL, ?, NULL)
        """,
        (
            attempt_id,
            session_id,
            turn_id,
            attempt_index,
            status,
            input_json,
            to_iso(started_at),
        ),
    )


def get_agent_attempt(connection: sqlite3.Connection, attempt_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM agent_attempts WHERE id = ?", (attempt_id,)).fetchone()
    return row_to_dict(row)


def list_agent_attempts(connection: sqlite3.Connection, turn_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM agent_attempts WHERE turn_id = ? ORDER BY attempt_index, id", (turn_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def finish_agent_attempt(
    connection: sqlite3.Connection,
    attempt_id: str,
    *,
    status: str,
    iteration: int,
    used_tokens: int,
    error_json: str | None,
    finished_at: datetime,
) -> int:
    cursor = connection.execute(
        """
        UPDATE agent_attempts
        SET status = ?, iteration = ?, used_tokens = ?, error_json = ?, finished_at = ?
        WHERE id = ?
        """,
        (status, iteration, used_tokens, error_json, to_iso(finished_at), attempt_id),
    )
    return cursor.rowcount


def update_agent_session(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    status: str,
    iteration: int | None = None,
    used_tokens: int | None = None,
    result_json: str | None = None,
    error_json: str | None = None,
    clear_error: bool = False,
    updated_at: datetime,
) -> int:
    fields = ["status = ?", "updated_at = ?"]
    values: list[object] = [status, to_iso(updated_at)]
    if iteration is not None:
        fields.append("iteration = ?")
        values.append(iteration)
    if used_tokens is not None:
        fields.append("used_tokens = ?")
        values.append(used_tokens)
    if result_json is not None:
        fields.append("result_json = ?")
        values.append(result_json)
    if error_json is not None:
        fields.append("error_json = ?")
        values.append(error_json)
    if clear_error:
        fields.append("error_json = NULL")
    values.append(session_id)
    cursor = connection.execute(
        f"UPDATE agent_sessions SET {', '.join(fields)} WHERE id = ?", values
    )
    return cursor.rowcount


def next_agent_event_sequence(connection: sqlite3.Connection, session_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) FROM agent_events WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row[0]) + 1


def append_agent_event(
    connection: sqlite3.Connection,
    session_id: str,
    event_type: str,
    payload_json: str,
    created_at: datetime,
    *,
    turn_id: str | None = None,
    attempt_id: str | None = None,
) -> int:
    sequence = next_agent_event_sequence(connection, session_id)
    connection.execute(
        """
        INSERT INTO agent_events(
            session_id, sequence, event_type, payload_json, created_at, turn_id, attempt_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, sequence, event_type, payload_json, to_iso(created_at), turn_id, attempt_id),
    )
    return sequence


def list_agent_events(connection: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT sequence, event_type, payload_json, created_at, turn_id, attempt_id
        FROM agent_events WHERE session_id = ? ORDER BY sequence
        """,
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_chat_session(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    status: str,
    history_json: str,
    pending_json: str | None,
    ticker: str | None,
    run_id: str | None,
    last_agent_session_id: str | None,
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO chat_sessions(
            id, status, ticker, run_id, last_agent_session_id, pending_json,
            history_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            status,
            ticker,
            run_id,
            last_agent_session_id,
            pending_json,
            history_json,
            to_iso(created_at),
            to_iso(created_at),
        ),
    )


def get_chat_session(connection: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    return row_to_dict(row)


def update_chat_session(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    status: str | None = None,
    history_json: str | None = None,
    pending_json: str | None = None,
    ticker: str | None = None,
    run_id: str | None = None,
    last_agent_session_id: str | None = None,
    clear_pending: bool = False,
    updated_at: datetime,
) -> int:
    fields = ["updated_at = ?"]
    values: list[object] = [to_iso(updated_at)]
    for name, value in (
        ("status", status),
        ("history_json", history_json),
        ("pending_json", pending_json),
        ("ticker", ticker),
        ("run_id", run_id),
        ("last_agent_session_id", last_agent_session_id),
    ):
        if value is not None:
            fields.append(f"{name} = ?")
            values.append(value)
    if clear_pending:
        fields.append("pending_json = NULL")
    values.append(session_id)
    cursor = connection.execute(
        f"UPDATE chat_sessions SET {', '.join(fields)} WHERE id = ?", values
    )
    return cursor.rowcount


def insert_interactive_report(
    connection: sqlite3.Connection,
    *,
    turn_id: str,
    session_id: str,
    attempt_id: str,
    ticker: str,
    report_json: str,
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO interactive_reports(turn_id, session_id, attempt_id, ticker, report_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (turn_id, session_id, attempt_id, ticker, report_json, to_iso(created_at)),
    )


def get_interactive_report(
    connection: sqlite3.Connection, session_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT * FROM interactive_reports
        WHERE session_id = ? ORDER BY created_at DESC, turn_id DESC LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    return row_to_dict(row)


def get_interactive_report_for_turn(
    connection: sqlite3.Connection, turn_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM interactive_reports WHERE turn_id = ?", (turn_id,)
    ).fetchone()
    return row_to_dict(row)


def list_interactive_reports(connection: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM interactive_reports
        WHERE session_id = ? ORDER BY created_at, turn_id
        """,
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def next_memo_version(connection: sqlite3.Connection, ticker: str, title: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM company_memos WHERE ticker = ? AND title = ?",
        (ticker, title),
    ).fetchone()
    return int(row[0]) + 1


def insert_company_memo(
    connection: sqlite3.Connection,
    *,
    memo_id: str,
    ticker: str,
    title: str,
    content: str,
    source_refs_json: str,
    created_from_session: str,
    created_from_turn: str | None,
    created_from_report_turn: str | None,
    tags_json: str,
    version: int,
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO company_memos(
            id, ticker, title, content, source_refs_json, created_from_session,
            created_from_turn, created_from_report_turn, tags_json, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memo_id,
            ticker,
            title,
            content,
            source_refs_json,
            created_from_session,
            created_from_turn,
            created_from_report_turn,
            tags_json,
            version,
            to_iso(created_at),
            to_iso(created_at),
        ),
    )


def list_company_memos(connection: sqlite3.Connection, ticker: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, ticker, title, content, source_refs_json, created_from_session,
               created_from_turn, created_from_report_turn, tags_json, version, created_at, updated_at
        FROM company_memos WHERE ticker = ? ORDER BY created_at DESC, id DESC
        """,
        (ticker,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_company_memo(connection: sqlite3.Connection, memo_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM company_memos WHERE id = ?", (memo_id,)).fetchone()
    return row_to_dict(row)


def get_agent_scratchpad(connection: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM agent_scratchpads WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row_to_dict(row)


def upsert_agent_scratchpad(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    revision: int,
    objective: str,
    plan_json: str,
    observations_json: str,
    calculation_refs_json: str,
    source_refs_json: str,
    open_questions_json: str,
    content_hash: str,
    compression_count: int,
    updated_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO agent_scratchpads(
            session_id, revision, objective, plan_json, observations_json,
            calculation_refs_json, source_refs_json, open_questions_json,
            content_hash, compression_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            revision = excluded.revision,
            objective = excluded.objective,
            plan_json = excluded.plan_json,
            observations_json = excluded.observations_json,
            calculation_refs_json = excluded.calculation_refs_json,
            source_refs_json = excluded.source_refs_json,
            open_questions_json = excluded.open_questions_json,
            content_hash = excluded.content_hash,
            compression_count = excluded.compression_count,
            updated_at = excluded.updated_at
        """,
        (
            session_id,
            revision,
            objective,
            plan_json,
            observations_json,
            calculation_refs_json,
            source_refs_json,
            open_questions_json,
            content_hash,
            compression_count,
            to_iso(updated_at),
        ),
    )


def upsert_memory_entry(
    connection: sqlite3.Connection,
    *,
    entry_id: str,
    ticker: str,
    memory_type: str,
    status: str,
    topic: str,
    as_of: str | None,
    content: str,
    source_refs_json: str,
    origin_ref: str,
    content_hash: str,
    created_at: datetime,
) -> None:
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO memory_entries(
                id, ticker, memory_type, status, topic, as_of, content,
                source_refs_json, origin_ref, content_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                ticker = excluded.ticker,
                memory_type = excluded.memory_type,
                status = excluded.status,
                topic = excluded.topic,
                as_of = excluded.as_of,
                content = excluded.content,
                source_refs_json = excluded.source_refs_json,
                origin_ref = excluded.origin_ref,
                content_hash = excluded.content_hash,
                updated_at = excluded.updated_at
            """,
            (
                entry_id,
                ticker,
                memory_type,
                status,
                topic,
                as_of,
                content,
                source_refs_json,
                origin_ref,
                content_hash,
                to_iso(created_at),
                to_iso(created_at),
            ),
        )
        connection.execute("DELETE FROM memory_entry_fts WHERE entry_id = ?", (entry_id,))
        connection.execute(
            "INSERT INTO memory_entry_fts(entry_id, ticker, topic, content) VALUES (?, ?, ?, ?)",
            (entry_id, ticker, topic, content),
        )


def get_memory_entry(connection: sqlite3.Connection, entry_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)).fetchone()
    return row_to_dict(row)


def search_memory_entries(
    connection: sqlite3.Connection, ticker: str, query: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """先用 FTS，再用同公司最近记录回退；后者保证中文分词差异不会丢失记忆。"""
    safe_limit = max(1, min(limit, 20))
    terms = [term.replace('"', " ").strip() for term in query.split() if term.strip()]
    if terms:
        match = " OR ".join(f'"{term}"' for term in terms)
        try:
            rows = connection.execute(
                """
                SELECT memory_entries.*
                FROM memory_entry_fts
                JOIN memory_entries ON memory_entries.id = memory_entry_fts.entry_id
                WHERE memory_entry_fts.ticker = ? AND memory_entry_fts MATCH ?
                ORDER BY memory_entries.updated_at DESC, memory_entries.id DESC
                LIMIT ?
                """,
                (ticker, match, safe_limit),
            ).fetchall()
            if rows:
                return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            pass
    rows = connection.execute(
        """
        SELECT * FROM memory_entries
        WHERE ticker = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (ticker, safe_limit),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_analysis_run(
    connection: sqlite3.Connection,
    *,
    analysis_id: str,
    session_id: str,
    turn_id: str | None = None,
    ticker: str,
    execution_index: int,
    status: str,
    dataset_hash: str,
    code_hash: str | None,
    sql_hash: str | None = None,
    limits_json: str,
    source_refs_json: str,
    workspace_path: str,
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO analysis_runs(
            id, session_id, turn_id, ticker, execution_index, status, dataset_hash, code_hash, sql_hash,
            limits_json, result_json, result_summary, source_refs_json, workspace_path,
            created_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL)
        """,
        (
            analysis_id,
            session_id,
            turn_id,
            ticker,
            execution_index,
            status,
            dataset_hash,
            code_hash,
            sql_hash,
            limits_json,
            source_refs_json,
            workspace_path,
            to_iso(created_at),
        ),
    )


def finish_analysis_run(
    connection: sqlite3.Connection,
    analysis_id: str,
    *,
    status: str,
    result_json: str | None,
    result_summary: str | None,
    finished_at: datetime,
) -> int:
    cursor = connection.execute(
        """
        UPDATE analysis_runs
        SET status = ?, result_json = ?, result_summary = ?, finished_at = ?
        WHERE id = ?
        """,
        (status, result_json, result_summary, to_iso(finished_at), analysis_id),
    )
    return cursor.rowcount


def get_analysis_run(connection: sqlite3.Connection, analysis_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (analysis_id,)).fetchone()
    return row_to_dict(row)


def list_analysis_runs(
    connection: sqlite3.Connection, session_id: str, *, turn_id: str | None = None
) -> list[dict[str, Any]]:
    if turn_id is None:
        rows = connection.execute(
            "SELECT * FROM analysis_runs WHERE session_id = ? ORDER BY execution_index, id",
            (session_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT * FROM analysis_runs
            WHERE session_id = ? AND turn_id = ?
            ORDER BY execution_index, id
            """,
            (session_id, turn_id),
        ).fetchall()
    return [dict(row) for row in rows]


def next_analysis_execution_index(
    connection: sqlite3.Connection, session_id: str, turn_id: str
) -> int:
    row = connection.execute(
        """
        SELECT COALESCE(MAX(execution_index), 0)
        FROM analysis_runs WHERE session_id = ? AND turn_id = ?
        """,
        (session_id, turn_id),
    ).fetchone()
    return int(row[0]) + 1


def insert_agent_evidence_receipt(
    connection: sqlite3.Connection,
    *,
    receipt_id: str,
    session_id: str,
    turn_id: str,
    analysis_id: str | None,
    tool_id: str,
    source_id: str,
    locator: str,
    relation: str,
    source_sha256: str,
    fact_id: str | None,
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO agent_evidence_receipts(
            id, session_id, turn_id, analysis_id, tool_id, source_id, locator,
            relation, source_sha256, fact_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt_id,
            session_id,
            turn_id,
            analysis_id,
            tool_id,
            source_id,
            locator,
            relation,
            source_sha256,
            fact_id,
            to_iso(created_at),
        ),
    )


def list_agent_evidence_receipts(
    connection: sqlite3.Connection, turn_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM agent_evidence_receipts
        WHERE turn_id = ? ORDER BY created_at, id
        """,
        (turn_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def has_agent_evidence_receipt(
    connection: sqlite3.Connection,
    *,
    turn_id: str,
    source_id: str,
    locator: str,
    source_sha256: str,
) -> bool:
    row = connection.execute(
        """
        SELECT 1 FROM agent_evidence_receipts
        WHERE turn_id = ? AND source_id = ? AND locator = ? AND source_sha256 = ?
        LIMIT 1
        """,
        (turn_id, source_id, locator, source_sha256),
    ).fetchone()
    return row is not None


def replace_source_conflicts(
    connection: sqlite3.Connection,
    run_id: str,
    rows: list[dict[str, Any]],
    *,
    created_at: datetime,
) -> None:
    connection.execute("DELETE FROM source_conflicts WHERE run_id = ?", (run_id,))
    for row in rows:
        connection.execute(
            """
            INSERT INTO source_conflicts(
                id, run_id, provider_source_id, official_source_id, metric, period, currency,
                provider_value, official_value, status, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                run_id,
                row["provider_source_id"],
                row.get("official_source_id"),
                row["metric"],
                row.get("period"),
                row.get("currency"),
                row.get("provider_value"),
                row.get("official_value"),
                row["status"],
                row["details_json"],
                to_iso(created_at),
            ),
        )


def list_source_conflicts(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM source_conflicts WHERE run_id = ? ORDER BY metric, period, id", (run_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def insert_trace(
    connection: sqlite3.Connection,
    *,
    trace_id: str,
    trace_kind: str,
    name: str,
    run_id: str | None,
    session_id: str | None,
    attributes_json: str,
    started_at: datetime,
    turn_id: str | None = None,
    attempt_id: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO traces(
            id, trace_kind, name, run_id, session_id, turn_id, attempt_id, attributes_json, status,
            error_json, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', NULL, ?, NULL)
        """,
        (
            trace_id,
            trace_kind,
            name,
            run_id,
            session_id,
            turn_id,
            attempt_id,
            attributes_json,
            to_iso(started_at),
        ),
    )


def finish_trace(
    connection: sqlite3.Connection,
    trace_id: str,
    *,
    attributes_json: str,
    status: str,
    error_json: str | None,
    finished_at: datetime,
) -> int:
    cursor = connection.execute(
        """
        UPDATE traces
        SET attributes_json = ?, status = ?, error_json = ?, finished_at = ?
        WHERE id = ?
        """,
        (attributes_json, status, error_json, to_iso(finished_at), trace_id),
    )
    return cursor.rowcount


def update_trace_association(
    connection: sqlite3.Connection,
    trace_id: str,
    *,
    turn_id: str,
    attempt_id: str,
) -> int:
    """为运行中顶层 trace 补充刚创建的 Turn/Attempt 关联。"""
    cursor = connection.execute(
        "UPDATE traces SET turn_id = ?, attempt_id = ? WHERE id = ?",
        (turn_id, attempt_id, trace_id),
    )
    return cursor.rowcount


def insert_trace_span(
    connection: sqlite3.Connection,
    *,
    span_id: str,
    trace_id: str,
    parent_span_id: str | None,
    span_kind: str,
    name: str,
    attributes_json: str,
    started_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO trace_spans(
            id, trace_id, parent_span_id, span_kind, name, attributes_json,
            status, error_json, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'running', NULL, ?, NULL)
        """,
        (
            span_id,
            trace_id,
            parent_span_id,
            span_kind,
            name,
            attributes_json,
            to_iso(started_at),
        ),
    )


def finish_trace_span(
    connection: sqlite3.Connection,
    span_id: str,
    *,
    attributes_json: str,
    status: str,
    error_json: str | None,
    finished_at: datetime,
) -> int:
    cursor = connection.execute(
        """
        UPDATE trace_spans
        SET attributes_json = ?, status = ?, error_json = ?, finished_at = ?
        WHERE id = ?
        """,
        (attributes_json, status, error_json, to_iso(finished_at), span_id),
    )
    return cursor.rowcount


def list_traces_for_run(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM traces WHERE run_id = ? ORDER BY started_at, id", (run_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def list_traces_for_session(connection: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM traces WHERE session_id = ? ORDER BY started_at, id", (session_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def list_trace_spans(connection: sqlite3.Connection, trace_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at, id", (trace_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def backup_database(root: Path, destination: Path) -> None:
    """用 SQLite backup API 生成一致性快照；调用方负责先取得项目写锁。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open_db(root) as source:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
