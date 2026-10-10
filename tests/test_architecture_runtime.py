from __future__ import annotations

import json
from pathlib import Path

import pytest

from wonder_stockresearch import agent, storage
from wonder_stockresearch.interactive import agent_model, legacy_agent
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.interactive.pi import pi_agent
from wonder_stockresearch.models import InteractiveFollowup, InteractiveQuestion


@pytest.mark.parametrize("initial,changed", [("legacy", "pi"), ("pi", "legacy")])
def test_resume_uses_original_runtime_and_followup_uses_new_selection(
    tmp_path: Path, monkeypatch, initial: str, changed: str
) -> None:
    calls = []
    legacy = legacy_agent.execute
    native = pi_agent.execute

    def run_legacy(*args, **kwargs):
        calls.append("legacy")
        return legacy(*args, **kwargs)

    def run_native(*args, **kwargs):
        calls.append("pi")
        return native(*args, **kwargs)

    monkeypatch.setattr(legacy_agent, "execute", run_legacy)
    monkeypatch.setattr(pi_agent, "execute", run_native)
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", initial)
    waiting = agent.ask(
        tmp_path,
        "SYNTHETIC",
        InteractiveQuestion(question="期间？"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "ask_user", "question": "哪一年？"}, 7)]
        ),
    )
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", changed)
    completed = agent.respond(
        tmp_path,
        waiting["session_id"],
        waiting["turn_id"],
        InteractiveFollowup(question="2025"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "final", "answer": "原问题完成"}, 9)]
        ),
    )
    followup = agent.followup(
        tmp_path,
        waiting["session_id"],
        InteractiveFollowup(question="新问题"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "final", "answer": "新问题完成"}, 11)]
        ),
    )
    assert calls == [initial, initial, changed]
    with storage.open_db(tmp_path) as connection:
        original = storage.get_agent_turn(connection, waiting["turn_id"])
        new = storage.get_agent_turn(connection, followup["turn_id"])
        assert original["used_tokens"] == 16 and original["iteration"] == 2
        assert new["used_tokens"] == 11 and new["iteration"] == 1
        assert (
            json.loads(
                storage.get_interactive_report_for_turn(connection, waiting["turn_id"])[
                    "report_json"
                ]
            )
            == completed["report"]
        )


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
@pytest.mark.parametrize(
    "code,recoverable", [("invalid_tool_arguments", True), ("unauthorized_tool", False)]
)
def test_both_runtimes_keep_recoverable_and_fatal_tool_errors_distinct(
    tmp_path: Path, monkeypatch, runtime: str, code: str, recoverable: bool
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    decisions = []

    def model(_root, messages, _model):
        decisions.append(messages)
        if len(decisions) == 1:
            return {
                "type": "tool_call",
                "tool": "query_archived_financials",
                "arguments": {"query": "夹具"},
            }, 10
        assert any("返回可恢复错误" in item["content"] for item in messages)
        return {"type": "final", "answer": "夹具执行失败，保留缺口。", "gaps": ["未取得数据"]}, 10

    def fail_tool(*_args):
        raise AgentError(code, "夹具工具错误", exit_code=2)

    monkeypatch.setattr(agent, "_execute_tool", fail_tool)
    call = lambda: agent.ask(
        tmp_path,
        "SYNTHETIC",
        InteractiveQuestion(question="离线错误分流"),
        model_client=agent_model.FunctionAgentModelClient(model),
    )
    if recoverable:
        result = call()
        assert result["status"] == "completed"
        assert len(decisions) == 2
        if runtime == "pi":
            with storage.open_db(tmp_path) as connection:
                messages = storage.list_agent_messages(connection, result["turn_id"])
            tool_result = next(item for item in messages if item["role"] == "toolResult")
            assert tool_result["isError"] is True
            assert json.loads(tool_result["content"][0]["text"])["code"] == code
    else:
        with pytest.raises(AgentError) as error:
            call()
        assert error.value.code == code and len(decisions) == 1
        with storage.open_db(tmp_path) as connection:
            turn = dict(connection.execute("SELECT * FROM agent_turns").fetchone())
        assert turn["used_tokens"] == 10 and turn["status"] == "failed"


@pytest.mark.parametrize("runtime", ["future-runtime", [], {}, True, None])
def test_unknown_frozen_runtime_rejects_without_starting_attempt(
    tmp_path: Path, monkeypatch, runtime: object
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "legacy")
    waiting = agent.ask(
        tmp_path,
        "SYNTHETIC",
        InteractiveQuestion(question="未知版本"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "ask_user", "question": "期间？"}, 5)]
        ),
    )
    with storage.open_db(tmp_path) as connection:
        turn = storage.get_agent_turn(connection, waiting["turn_id"])
        payload = json.loads(turn["question_json"])
        payload["_agent_runtime"] = runtime
        connection.execute(
            "UPDATE agent_turns SET question_json=? WHERE id=?",
            (json.dumps(payload), waiting["turn_id"]),
        )
    before = agent.get_status(tmp_path, waiting["session_id"])
    with pytest.raises(AgentError) as error:
        agent.respond(
            tmp_path,
            waiting["session_id"],
            waiting["turn_id"],
            InteractiveFollowup(question="2025"),
        )
    assert error.value.code == "unsupported_turn_runtime"
    assert agent.get_status(tmp_path, waiting["session_id"]) == before
