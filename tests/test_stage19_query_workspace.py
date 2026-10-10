from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.test_stage18_agent import financial_run, make_root
from wonder_stockresearch import agent, storage
from wonder_stockresearch.interactive import query_workspace
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import InteractiveFollowup, InteractiveQuestion


def _turn_row(root: Path) -> tuple[dict[str, object], str]:
    run_id, _source_id = financial_run(root)
    session_id = "00000000-0000-4000-8000-000000000019"
    turn_id = "00000000-0000-4000-8000-000000000020"
    created_at = storage.now_utc()
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.insert_agent_session(
            connection,
            session_id=session_id,
            ticker="SYNTHETIC",
            run_id=run_id,
            question_json=InteractiveQuestion(
                question="分析净利润", run_id=run_id
            ).model_dump_json(),
            status="active",
            provider="deepseek",
            model="test",
            max_iterations=6,
            token_budget=1000,
            created_at=created_at,
        )
        storage.insert_agent_turn(
            connection,
            turn_id=turn_id,
            session_id=session_id,
            ticker="SYNTHETIC",
            run_id=run_id,
            question_json=json.dumps({"question": "分析净利润"}),
            skill_audit_json="[]",
            allowed_tools_json=json.dumps(["run_readonly_sql"]),
            tool_registry_version="4",
            provider="deepseek",
            model="test",
            budget_json=json.dumps({"max_iterations": 6, "token_budget": 1000}),
            created_at=created_at,
        )
        row = storage.get_agent_session(connection, session_id)
    assert row is not None
    return row, turn_id


def test_query_workspace_uses_turn_scoped_readonly_snapshot_and_receipts(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    row, turn_id = _turn_row(root)

    schema = query_workspace.describe_schema(root, row, turn_id)
    assert schema["fact_count"] == 2
    assert schema["tables"]["financial_facts"][0] == "fact_id"  # type: ignore[index]

    sql = """
        SELECT fact_id, label, value_decimal, period, source_id, locator
        FROM financial_facts ORDER BY period
    """
    result = query_workspace.run_readonly_sql(root, row, turn_id, sql)
    assert result["status"] == "sql_succeeded"
    assert result["row_count"] == 2
    assert result["citable"] is True
    assert len(result["citable_refs"]) == 2  # type: ignore[arg-type]

    analysis_id = str(result["analysis_id"])
    function = query_workspace.run_analysis_function(
        root,
        row,
        turn_id,
        analysis_id,
        """def analyze(rows):
    totals = {}
    for row in rows:
        totals[row[\"period\"]] = float(row[\"value_decimal\"])
    return {\"count\": len(rows), \"totals\": totals}
""",
    )
    assert function["result"] == {"count": 2, "totals": {"2024/FY": 100.0, "2025/FY": 120.0}}
    artifact = query_workspace.read_analysis_artifact(root, row, turn_id, analysis_id)
    assert artifact["status"] == "function_succeeded"

    assert (
        query_workspace.record_tool_evidence_receipts(
            root, row, turn_id, "run_readonly_sql", result
        )
        == 2
    )
    with storage.open_db(root) as connection:
        receipts = storage.list_agent_evidence_receipts(connection, turn_id)
    assert {receipt["fact_id"] for receipt in receipts} == {
        ref["fact_id"]
        for ref in result["citable_refs"]  # type: ignore[index]
    }
    workspace = root / "data" / "tmp" / "analysis" / str(row["id"]) / turn_id / analysis_id
    assert (workspace / "snapshot.db").stat().st_mode & 0o222 == 0
    assert (workspace / "sql-result.json").is_file()


def test_query_workspace_rejects_privileged_sql_and_unsafe_function(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    row, turn_id = _turn_row(root)
    with pytest.raises(query_workspace.QueryWorkspaceError) as error:
        query_workspace.run_readonly_sql(root, row, turn_id, "PRAGMA query_only")
    assert error.value.code == "analysis_sql_not_allowed"

    with pytest.raises(query_workspace.QueryWorkspaceError) as error:
        query_workspace.run_readonly_sql(root, row, turn_id, "SELECT name FROM sqlite_master")
    assert error.value.code == "analysis_sql_failed"
    with storage.open_db(root) as connection:
        failed = storage.list_analysis_runs(connection, str(row["id"]), turn_id=turn_id)
    assert failed[0]["status"] == "sql_failed"

    with pytest.raises(query_workspace.QueryWorkspaceError) as error:
        query_workspace.validate_function("def analyze(rows):\n    import os\n    return {}\n")
    assert error.value.code == "analysis_function_not_allowed"


def test_query_workspace_enforces_per_turn_sql_and_function_budgets(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    row, turn_id = _turn_row(root)
    sql = "SELECT fact_id, source_id, locator FROM financial_facts LIMIT 1"
    for _ in range(6):
        query_workspace.run_readonly_sql(root, row, turn_id, sql)
    with pytest.raises(query_workspace.QueryWorkspaceError) as error:
        query_workspace.run_readonly_sql(root, row, turn_id, sql)
    assert error.value.code == "analysis_sql_limit"

    function_parent = tmp_path / "function-budget"
    function_parent.mkdir()
    function_root = make_root(function_parent)
    function_row, function_turn_id = _turn_row(function_root)
    for _ in range(3):
        sql_result = query_workspace.run_readonly_sql(
            function_root, function_row, function_turn_id, sql
        )
        query_workspace.run_analysis_function(
            function_root,
            function_row,
            function_turn_id,
            str(sql_result["analysis_id"]),
            'def analyze(rows):\n    return {"count": len(rows)}\n',
        )
    next_sql = query_workspace.run_readonly_sql(function_root, function_row, function_turn_id, sql)
    with pytest.raises(query_workspace.QueryWorkspaceError) as error:
        query_workspace.run_analysis_function(
            function_root,
            function_row,
            function_turn_id,
            str(next_sql["analysis_id"]),
            'def analyze(rows):\n    return {"count": len(rows)}\n',
        )
    assert error.value.code == "analysis_function_limit"


def test_agent_recovers_from_query_error_then_requires_current_turn_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    run_id, source_id = financial_run(root)
    calls = 0

    def fake_call(
        _root: Path, messages: list[dict[str, str]], _model: str
    ) -> tuple[dict[str, object], int]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "type": "tool_call",
                "tool": "run_readonly_sql",
                "arguments": {"sql": "PRAGMA cache_size"},
            }, 10
        if calls == 2:
            assert "可恢复错误" in messages[-1]["content"]
            return {
                "type": "tool_call",
                "tool": "run_readonly_sql",
                "arguments": {
                    "sql": "SELECT fact_id, source_id, locator FROM financial_facts LIMIT 1"
                },
            }, 10
        tool_result = (
            messages[-1]["content"].split(" 返回：", maxsplit=1)[1].split("\n请继续", maxsplit=1)[0]
        )
        raw_reference = json.loads(tool_result)["citable_refs"][0]
        reference = {key: raw_reference[key] for key in ("source_id", "locator", "relation")}
        return {
            "type": "final",
            "answer": "已基于当前 Turn 的财务快照核对。",
            "claims": [{"text": "该数值来自当前快照。", "evidence": [reference]}],
            "evidence_refs": [],
            "counterarguments": [],
            "gaps": [],
            "suggested_next_checks": [],
        }, 10

    monkeypatch.setattr(agent, "_api_call_for_root", fake_call)
    result = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="净利润是多少？", run_id=run_id, max_iterations=5),
        tool_names=["run_readonly_sql"],
    )
    assert result["status"] == "completed"
    status = agent.get_status(root, str(result["session_id"]))
    assert status["turns"][0]["turn_id"] == result["turn_id"]
    with storage.open_db(root) as connection:
        assert storage.list_agent_evidence_receipts(connection, str(result["turn_id"]))

    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        lambda _root, _messages, _model: (
            {
                "type": "final",
                "answer": "这个 follow-up 没有新的事实引用。",
                "claims": [],
                "evidence_refs": [],
                "counterarguments": [],
                "gaps": [],
                "suggested_next_checks": [],
            },
            10,
        ),
    )
    followup = agent.followup(
        root, str(result["session_id"]), InteractiveFollowup(question="再核对一次")
    )
    assert followup["status"] == "completed"
    assert followup["turn_id"] != result["turn_id"]
    assert len(agent.get_status(root, str(result["session_id"]))["turns"]) == 2

    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        lambda _root, _messages, _model: (
            {
                "type": "final",
                "answer": "没有工具回执。",
                "claims": [
                    {
                        "text": "没有工具回执。",
                        "evidence": [
                            {"source_id": source_id, "locator": "$.payload", "relation": "supports"}
                        ],
                    }
                ],
                "evidence_refs": [],
                "counterarguments": [],
                "gaps": [],
                "suggested_next_checks": [],
            },
            10,
        ),
    )
    with pytest.raises(AgentError, match="当前 Turn") as error:
        agent.ask(
            root,
            "SYNTHETIC",
            InteractiveQuestion(question="再问一次", run_id=run_id),
            tool_names=["run_readonly_sql"],
        )
    assert error.value.code == "missing_turn_evidence_receipt"


def test_v9_to_v10_migration_backfills_legacy_turn(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    database = storage.db_path(root)
    database.parent.mkdir(parents=True)
    session_id = "00000000-0000-4000-8000-000000000091"
    analysis_id = "00000000-0000-4000-8000-000000000092"
    now = datetime(2026, 10, 6, tzinfo=UTC).isoformat()
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE stocks (ticker TEXT PRIMARY KEY, name TEXT, watched INTEGER, created_at TEXT);
            CREATE TABLE runs (id TEXT PRIMARY KEY);
            CREATE TABLE sources (id TEXT PRIMARY KEY);
            CREATE TABLE agent_sessions (
                id TEXT PRIMARY KEY, ticker TEXT, run_id TEXT, question_json TEXT, status TEXT,
                provider TEXT, model TEXT, max_iterations INTEGER, iteration INTEGER,
                token_budget INTEGER, used_tokens INTEGER, result_json TEXT, error_json TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE analysis_runs (
                id TEXT PRIMARY KEY, session_id TEXT, ticker TEXT, execution_index INTEGER,
                status TEXT, dataset_hash TEXT, code_hash TEXT, limits_json TEXT, result_json TEXT,
                result_summary TEXT, source_refs_json TEXT, workspace_path TEXT,
                created_at TEXT, finished_at TEXT
            );
            """
        )
        connection.execute("INSERT INTO stocks VALUES (?, NULL, 0, ?)", ("SYNTHETIC", now))
        connection.execute(
            """INSERT INTO agent_sessions VALUES (?, ?, NULL, ?, 'completed', 'deepseek', 'test',
               4, 1, 200, 10, NULL, NULL, ?, ?)""",
            (session_id, "SYNTHETIC", '{"question":"旧问题"}', now, now),
        )
        connection.execute(
            """INSERT INTO analysis_runs VALUES (?, ?, ?, 1, 'succeeded', 'dataset', NULL, '{}',
               NULL, NULL, '[]', 'data/tmp/old', ?, ?)""",
            (analysis_id, session_id, "SYNTHETIC", now, now),
        )
        connection.execute("PRAGMA user_version = 9")
        connection.commit()
    finally:
        connection.close()

    storage.initialize(root)
    with storage.open_db(root) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
        legacy = storage.get_agent_turn(connection, f"legacy-{session_id}")
        analysis = storage.get_analysis_run(connection, analysis_id)
    assert legacy is not None
    assert json.loads(str(legacy["budget_json"])) == {"max_iterations": 4, "token_budget": 200}
    assert analysis is not None and analysis["turn_id"] == f"legacy-{session_id}"
