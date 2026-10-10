from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from wonder_stockresearch import agent, model_request, storage
from wonder_stockresearch.interactive import agent_model
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.interactive.agent_model import ScriptedAgentModelClient
from wonder_stockresearch.models import InteractiveFollowup, InteractiveQuestion


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'runtime-test'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def final(answer: str = "已完成临时回答。") -> tuple[dict[str, object], int]:
    return (
        {
            "type": "final",
            "answer": answer,
            "claims": [],
            "evidence_refs": [],
            "counterarguments": [],
            "gaps": [],
            "suggested_next_checks": [],
        },
        7,
    )


def test_respond_reuses_waiting_turn_and_keeps_attempt_timeline(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    client = ScriptedAgentModelClient(
        [
            ({"type": "ask_user", "question": "你更关注未来一年还是三年？"}, 5),
            final("基于你补充的三年期限，这是临时判断。"),
        ]
    )

    waiting = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="护城河应如何继续核对？"),
        model_client=client,
    )
    assert waiting["status"] == "waiting_user"
    session_id = str(waiting["session_id"])
    turn_id = str(waiting["turn_id"])

    completed = agent.respond(
        root,
        session_id,
        turn_id,
        InteractiveFollowup(question="未来三年。"),
        model_client=client,
    )
    assert completed["status"] == "completed"
    assert completed["turn_id"] == turn_id
    status = agent.get_status(root, session_id)
    turn = status["turns"][0]
    assert turn["status"] == "succeeded"
    assert [item["status"] for item in turn["attempts"]] == ["waiting_user", "succeeded"]
    assert turn["used_tokens"] == 12
    assert status["reports"][0]["turn_id"] == turn_id

    with storage.open_db(root) as connection:
        traces = connection.execute(
            "SELECT turn_id, attempt_id FROM traces WHERE session_id = ? ORDER BY started_at",
            (session_id,),
        ).fetchall()
    assert len(traces) == 2
    assert all(row["turn_id"] == turn_id and row["attempt_id"] for row in traces)


def test_retry_preserves_turn_budget_and_cannot_reset_it(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    exhausted_client = ScriptedAgentModelClient([({"type": "final"}, 256)])
    with pytest.raises(AgentError, match="决策"):
        agent.ask(
            root,
            "SYNTHETIC",
            InteractiveQuestion(question="先失败一次", token_budget=256),
            model_client=exhausted_client,
        )

    with storage.open_db(root) as connection:
        session_id = str(connection.execute("SELECT id FROM agent_sessions").fetchone()[0])
        turn_id = str(connection.execute("SELECT id FROM agent_turns").fetchone()[0])

    with pytest.raises(AgentError, match="既定预算"):
        agent.retry(
            root,
            session_id,
            turn_id,
            model_client=ScriptedAgentModelClient([final()]),
        )
    status = agent.get_status(root, session_id)
    turn = status["turns"][0]
    assert turn["status"] == "budget_exhausted"
    assert turn["used_tokens"] == 256
    assert len(turn["attempts"]) == 1


def test_followup_creates_new_turn_and_does_not_overwrite_prior_report(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    first = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="第一个问题"),
        model_client=ScriptedAgentModelClient([final("第一个回答")]),
    )
    second = agent.followup(
        root,
        str(first["session_id"]),
        InteractiveFollowup(question="第二个问题"),
        model_client=ScriptedAgentModelClient([final("第二个回答")]),
    )
    status = agent.get_status(root, str(first["session_id"]))
    assert second["turn_id"] != first["turn_id"]
    assert [item["turn_id"] for item in status["reports"]] == [first["turn_id"], second["turn_id"]]
    assert [item["report"]["answer"] for item in status["reports"]] == ["第一个回答", "第二个回答"]


def test_cli_exposes_targeted_turn_commands() -> None:
    from wonder_stockresearch import cli

    parser = cli.build_parser()
    response_args = parser.parse_args(
        ["ask-respond", "session-id", "turn-id", "--input", "response.json"]
    )
    retry_args = parser.parse_args(["ask-retry", "session-id", "turn-id"])
    assert response_args.command == "ask-respond"
    assert retry_args.command == "ask-retry"


def only_turn(root: Path) -> tuple[str, str]:
    with storage.open_db(root) as connection:
        row = connection.execute("SELECT session_id, id FROM agent_turns").fetchone()
    return str(row[0]), str(row[1])


@pytest.mark.parametrize("failing_write", ["insert_interactive_report", "upsert_memory_entry"])
def test_final_commit_failure_never_leaves_success_without_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_write: str
) -> None:
    root = make_root(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("injected failure")

    with monkeypatch.context() as patch:
        patch.setattr(storage, failing_write, fail)
        with pytest.raises(AgentError, match="执行失败"):
            agent.ask(
                root,
                "SYNTHETIC",
                InteractiveQuestion(question="事务故障"),
                model_client=ScriptedAgentModelClient([final()]),
            )
    session_id, turn_id = only_turn(root)
    status = agent.get_status(root, session_id)
    assert status["status"] == "failed"
    assert status["turns"][0]["attempts"][0]["used_tokens"] == 7
    assert status["reports"] == []
    with storage.open_db(root) as connection:
        assert connection.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM memory_entry_fts").fetchone()[0] == 0
    agent.retry(root, session_id, turn_id, model_client=ScriptedAgentModelClient([final()]))
    assert agent.get_status(root, session_id)["turns"][0]["used_tokens"] == 14


def test_keyboard_interrupt_preserves_progress_and_retry_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)

    def cancel(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(agent, "_execute_tool", cancel)
        with pytest.raises(AgentError) as exc:
            agent.ask(
                root,
                "SYNTHETIC",
                InteractiveQuestion(question="中断恢复"),
                model_client=ScriptedAgentModelClient(
                    [({"type": "tool_call", "tool": "list_sources", "arguments": {}}, 9)]
                ),
            )
    assert exc.value.exit_code == 130
    session_id, turn_id = only_turn(root)
    first = agent.get_status(root, session_id)["turns"][0]
    assert first["status"] == "interrupted"
    assert first["attempts"][0]["used_tokens"] == 9
    agent.retry(root, session_id, turn_id, model_client=ScriptedAgentModelClient([final()]))
    assert agent.get_status(root, session_id)["turns"][0]["used_tokens"] == 16


def test_unknown_model_usage_is_not_free_after_failure(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    with pytest.raises(AgentError):
        agent.ask(
            root,
            "SYNTHETIC",
            InteractiveQuestion(question="未知用量", token_budget=256),
            model_client=ScriptedAgentModelClient([]),
        )
    session_id, turn_id = only_turn(root)
    assert agent.get_status(root, session_id)["turns"][0]["used_tokens"] == 256
    with pytest.raises(AgentError, match="既定预算"):
        agent.retry(root, session_id, turn_id, model_client=ScriptedAgentModelClient([final()]))


def test_over_budget_final_is_rejected_and_actual_usage_preserved(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    payload, _ = final()
    with pytest.raises(AgentError, match="超过 Turn 预算"):
        agent.ask(
            root,
            "SYNTHETIC",
            InteractiveQuestion(question="超预算", token_budget=256),
            model_client=ScriptedAgentModelClient([(payload, 300)]),
        )
    session_id, _ = only_turn(root)
    status = agent.get_status(root, session_id)
    assert status["reports"] == []
    assert status["turns"][0]["status"] == "budget_exhausted"
    assert status["turns"][0]["attempts"][0]["used_tokens"] == 300


def test_retry_reuses_successful_tool_and_user_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    waiting = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="原问题"),
        model_client=ScriptedAgentModelClient([({"type": "ask_user", "question": "期间？"}, 5)]),
    )
    session_id, turn_id = str(waiting["session_id"]), str(waiting["turn_id"])
    call = {"type": "tool_call", "tool": "list_sources", "arguments": {}}
    with pytest.raises(AgentError, match="决策"):
        agent.respond(
            root,
            session_id,
            turn_id,
            InteractiveFollowup(question="未来三年"),
            model_client=ScriptedAgentModelClient([(call, 8), ({"type": "final"}, 3)]),
        )

    def unexpected_tool(*args: object, **kwargs: object) -> None:
        raise AssertionError("成功工具不应重新执行")

    seen: list[str] = []
    decisions = iter([(call, 4), final()])

    def decide(root: Path, messages: list[dict[str, str]], model: str):
        seen.append(messages[1]["content"])
        return next(decisions)

    monkeypatch.setattr(agent, "_execute_tool", unexpected_tool)
    agent.retry(
        root, session_id, turn_id, model_client=agent.agent_model.FunctionAgentModelClient(decide)
    )
    assert "未来三年" in seen[0]
    assert agent.get_status(root, session_id)["turns"][0]["used_tokens"] == 27


def test_orphan_interrupt_refuses_live_execution_and_preserves_counters(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    with pytest.raises(AgentError):
        agent.ask(
            root,
            "SYNTHETIC",
            InteractiveQuestion(question="遗留执行"),
            model_client=ScriptedAgentModelClient([({"type": "final"}, 7)]),
        )
    session_id, turn_id = only_turn(root)
    _, _, attempt_id = agent._start_attempt(
        root, session_id, turn_id, kind="retry", input_payload={}
    )
    with (
        storage.agent_execution_lock(root, session_id),
        pytest.raises(storage.StorageError, match="执行进程"),
    ):
        agent.interrupt(root, session_id, turn_id)
    assert agent.interrupt(root, session_id, turn_id)["status"] == "interrupted"
    with storage.open_db(root) as connection:
        assert storage.get_agent_attempt(connection, attempt_id)["status"] == "interrupted"
    agent.retry(root, session_id, turn_id, model_client=ScriptedAgentModelClient([final()]))
    assert agent.get_status(root, session_id)["turns"][0]["used_tokens"] == 14


def test_v9_migration_preserves_reports_and_budget(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    session_id, report_time = str(uuid4()), storage.now_utc()
    with storage.open_db(root) as connection:
        end = (
            len(storage._SCHEMA)
            - len(storage._QUERY_WORKSPACE_DDL)
            - len(storage._AGENT_RUNTIME_DDL)
            - len(storage._AGENT_RECOVERY_DDL)
            - len(storage._PI_MESSAGES_DDL)
        )
        for statement in storage._SCHEMA[:end]:
            connection.execute(statement)
        storage.ensure_stock(connection, "SYNTHETIC", report_time)
        storage.insert_agent_session(
            connection,
            session_id=session_id,
            ticker="SYNTHETIC",
            run_id=None,
            question_json=InteractiveQuestion(question="历史问题").model_dump_json(),
            status="completed",
            provider="deepseek",
            model="fixture",
            max_iterations=6,
            token_budget=1000,
            created_at=report_time,
        )
        old_report = json.dumps({"answer": "历史回答"}, ensure_ascii=False)
        connection.execute(
            "INSERT INTO interactive_reports VALUES (?, ?, ?, ?, ?)",
            (
                session_id,
                "SYNTHETIC",
                old_report,
                storage.to_iso(report_time),
                storage.to_iso(report_time),
            ),
        )
        connection.execute("PRAGMA user_version = 9")
        connection.execute(
            "UPDATE agent_sessions SET iteration = 2, used_tokens = 37 WHERE id = ?", (session_id,)
        )
    storage.initialize(root)
    storage.initialize(root)
    with storage.open_db(root) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
        report = storage.get_interactive_report(connection, session_id)
        assert report["turn_id"] == f"legacy-{session_id}"
        assert report["report_json"] == old_report
        turn = storage.get_agent_turn(connection, f"legacy-{session_id}")
        assert turn["status"] == "succeeded"
        assert turn["used_tokens"] == 37 and turn["iteration"] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert agent.get_status(root, session_id)["reports"][0]["report"]["answer"] == "历史回答"


def test_attempt_creation_failure_rolls_back_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("injected attempt event failure")

    with pytest.raises(AgentError):
        agent.ask(
            root,
            "SYNTHETIC",
            InteractiveQuestion(question="重试创建故障"),
            model_client=ScriptedAgentModelClient([({"type": "final"}, 7)]),
        )
    session_id, turn_id = only_turn(root)
    with monkeypatch.context() as patch:
        patch.setattr(storage, "append_agent_event", fail)
        with pytest.raises(sqlite3.OperationalError):
            agent._start_attempt(root, session_id, turn_id, kind="retry", input_payload={})
    turn = agent.get_status(root, session_id)["turns"][0]
    assert turn["status"] == "failed"
    assert len(turn["attempts"]) == 1


def test_api_budget_caps_output_and_charges_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    captured: dict[str, object] = {}

    def request(root: Path, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "broken json"}}], "usage": {"total_tokens": 23}}

    monkeypatch.setattr(model_request, "api_key", lambda: "offline-placeholder")
    monkeypatch.setattr(model_request, "traced_request", request)
    reservation = agent_model.remaining_request_tokens.set(100)
    try:
        with pytest.raises(AgentError) as exc:
            agent._api_call_for_root(root, [{"role": "user", "content": "hello"}], "fixture")
        assert exc.value.consumed_tokens == 23
        assert captured["max_tokens"] == 66
    finally:
        agent_model.remaining_request_tokens.reset(reservation)


def test_memory_and_fts_update_roll_back_together(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    storage.initialize(root)
    with storage.open_db(root) as connection:
        storage.ensure_stock(connection, "SYNTHETIC", storage.now_utc())
        values = {
            "entry_id": "memo:fixture",
            "ticker": "SYNTHETIC",
            "memory_type": "memo",
            "status": "trusted",
            "topic": "测试",
            "as_of": None,
            "content": "old ROE",
            "source_refs_json": "[]",
            "origin_ref": "fixture",
            "content_hash": "old",
            "created_at": storage.now_utc(),
        }
        storage.upsert_memory_entry(connection, **values)
        connection.execute("""CREATE TRIGGER deny_fts_insert BEFORE INSERT ON memory_entry_fts_content
                              BEGIN SELECT RAISE(ABORT, 'injected fts failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            storage.upsert_memory_entry(connection, **{**values, "content": "new revenue"})
        assert storage.get_memory_entry(connection, "memo:fixture")["content"] == "old ROE"
        assert connection.execute("SELECT content FROM memory_entry_fts").fetchone()[0] == "old ROE"


def test_v11_migration_failure_rolls_back_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    storage.initialize(root)
    with storage.open_db(root) as connection:
        connection.execute("DROP TABLE agent_messages")
        connection.execute("PRAGMA user_version = 11")
    broken = {
        **storage._MIGRATIONS,
        12: ("CREATE TABLE migration_probe(id INTEGER)", "INSERT INTO missing_table VALUES (1)"),
    }
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_MIGRATIONS", broken)
        with pytest.raises(sqlite3.OperationalError):
            storage.initialize(root)
    with storage.open_db(root) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 11
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'migration_probe'"
            ).fetchone()
            is None
        )
    storage.initialize(root)
