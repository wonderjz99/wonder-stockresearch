from __future__ import annotations

import sqlite3
from pathlib import Path

from wonder_stockresearch import research, storage

_V1_SCHEMA = (
    """
    CREATE TABLE stocks (
        ticker TEXT PRIMARY KEY, name TEXT, watched INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY, ticker TEXT NOT NULL REFERENCES stocks(ticker),
        kind TEXT NOT NULL DEFAULT 'standard', parent_run_id TEXT REFERENCES runs(id),
        as_of TEXT NOT NULL, baseline_run_id TEXT REFERENCES runs(id), status TEXT NOT NULL,
        draft_json TEXT, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, completed_at TEXT, error_json TEXT,
        artifact_hashes_json TEXT
    )
    """,
    """
    CREATE TABLE sources (
        id TEXT PRIMARY KEY, ticker TEXT NOT NULL REFERENCES stocks(ticker), kind TEXT NOT NULL,
        metadata_json TEXT NOT NULL, sha256 TEXT NOT NULL, raw_path TEXT NOT NULL,
        text_path TEXT, retrieved_at TEXT NOT NULL, source_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE run_sources (
        run_id TEXT NOT NULL REFERENCES runs(id),
        source_id TEXT NOT NULL REFERENCES sources(id),
        PRIMARY KEY (run_id, source_id)
    )
    """,
)


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def create_v1_database(root: Path) -> None:
    database = root / "data" / "wonder.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        for statement in _V1_SCHEMA:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO stocks(ticker, name, watched, created_at) VALUES ('HK.00728', NULL, 0, ?)",
            ("2026-01-01T00:00:00+00:00",),
        )
        connection.commit()
    finally:
        connection.close()


_RUN_STEPS_V2_DDL = """
    CREATE TABLE run_steps (
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


def _step_columns(root: Path) -> set[str]:
    with storage.open_db(root) as connection:
        return {row[1] for row in connection.execute("PRAGMA table_info(run_steps)")}


def create_v2_database(root: Path) -> None:
    database = root / "data" / "wonder.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        for statement in _V1_SCHEMA:
            connection.execute(statement)
        connection.execute(_RUN_STEPS_V2_DDL)
        connection.execute("PRAGMA user_version = 2")
        connection.execute(
            "INSERT INTO stocks(ticker, name, watched, created_at) VALUES ('HK.00728', NULL, 0, ?)",
            ("2026-01-01T00:00:00+00:00",),
        )
        connection.execute(
            "INSERT INTO runs(id, ticker, as_of, status, schema_version, created_at, updated_at)"
            " VALUES ('r1', 'HK.00728', ?, 'draft', 2, ?, ?)",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO run_steps(run_id, step_key, attempt, status) "
            "VALUES ('r1', 'collect', 1, 'succeeded')"
        )
        connection.commit()
    finally:
        connection.close()


def test_initialize_migrates_v1_database_to_run_steps(tmp_path: Path) -> None:
    """Skipping the explicit migration or rebuilding tables must fail this test."""
    root = make_root(tmp_path)
    create_v1_database(root)

    storage.initialize(root)

    with storage.open_db(root) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        stock = connection.execute("SELECT ticker FROM stocks").fetchone()
    assert version == storage.SCHEMA_VERSION
    assert "run_steps" in tables
    assert {"agent_sessions", "agent_events", "interactive_reports", "company_memos"} <= tables
    assert {"agent_scratchpads", "memory_entries", "analysis_runs", "source_conflicts"} <= tables
    assert "metadata_json" in _step_columns(root)
    assert stock[0] == "HK.00728"


def test_initialize_migrates_v2_steps_adding_metadata_column(tmp_path: Path) -> None:
    """Skipping the v2→v3 migration must fail this test."""
    root = make_root(tmp_path)
    create_v2_database(root)
    assert "metadata_json" not in _step_columns(root)

    storage.initialize(root)

    with storage.open_db(root) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        step = connection.execute("SELECT step_key, status FROM run_steps").fetchone()
    assert version == storage.SCHEMA_VERSION
    assert "metadata_json" in _step_columns(root)
    assert tuple(step) == ("collect", "succeeded")


def test_run_steps_attempt_increments_and_status_surfaces_them(tmp_path: Path) -> None:
    """Losing attempt numbering or hiding steps from status must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "HK.00728")["run_id"])

    with storage.open_db(root) as connection:
        assert storage.next_step_attempt(connection, run_id, "market_snapshot") == 1
        storage.insert_step(
            connection,
            run_id,
            "market_snapshot",
            1,
            status="running",
            provider="futu",
            input_hash="abc",
            started_at=storage.now_utc(),
        )
        assert storage.next_step_attempt(connection, run_id, "market_snapshot") == 2
        assert (
            storage.finish_step(
                connection,
                run_id,
                "market_snapshot",
                1,
                status="failed",
                error_json='{"code": "network_error"}',
                finished_at=storage.now_utc(),
            )
            == 1
        )

    status = research.get_status(root, run_id)
    assert status["steps"][0]["step_key"] == "market_snapshot"
    assert status["steps"][0]["status"] == "failed"
    assert status["steps"][0]["error_json"] == '{"code": "network_error"}'
