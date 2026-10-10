from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from wonder_stockresearch import agent, storage
from wonder_stockresearch.interactive import agent_model
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.interactive.pi import pi_bridge, pi_context
from wonder_stockresearch.models import InteractiveFollowup, InteractiveQuestion


def test_native_message_projection_and_orphan_recovery() -> None:
    call = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "hidden", "signature": "secret"},
            {"type": "toolCall", "id": "native-1", "name": "probe", "arguments": {"期间": "2025"}},
        ],
        "diagnostics": "secret",
    }
    result = {
        "role": "toolResult",
        "toolCallId": "native-1",
        "toolName": "probe",
        "isError": False,
        "content": [{"type": "text", "text": "摘要"}],
        "details": {"raw": "secret"},
    }
    safe = [pi_context.visible(call), pi_context.visible(result)]
    assert "secret" not in json.dumps(safe) and "hidden" not in json.dumps(safe)
    assert pi_context.groups(safe) == [safe]
    assert pi_context.groups(safe[:1]) == []
    assert pi_context.groups(safe[1:]) == []


def test_facts_projection_keeps_values_before_duplicate_catalog() -> None:
    original = {
        "mode": "facts",
        "records": [{"label": "公司名称", "value": "测试公司", "locator": "$.profile[1]"}],
        "field_catalog": [{"label": "说明" * 400} for _ in range(40)],
    }
    result = json.loads(pi_context.tool_projection(original, token_limit=300))
    assert result["records"] == original["records"]
    assert result["field_catalog_omitted"] == 40
    assert len(original["field_catalog"]) == 40


def test_long_company_narrative_is_marked_excerpt_and_keeps_json_valid() -> None:
    original = {"rows": [{"fact_id": "fact", "value_json": json.dumps("公司业务" * 9000)}]}
    projected = json.loads(pi_context.tool_projection(original))
    item = projected["rows"][0]
    assert item["fact_id"] == "fact" and item["value_json_truncated"] is True
    assert len(json.loads(item["value_json"])) < 1250
    assert projected["context_truncated"] is True
    assert len(json.loads(original["rows"][0]["value_json"])) == 36000


def test_run_status_projects_counts_instead_of_full_draft_sections() -> None:
    result = json.loads(
        pi_context.tool_projection(
            {
                "id": "run",
                "status": "draft",
                "steps": [{"payload": "原文" * 10000}],
                "draft_sections": {"Q01": "原文" * 10000},
            }
        )
    )
    assert result["status"] == "draft" and result["steps_summary"] == {"count": 1}
    assert result["draft_sections_summary"] == {"count": 1}
    assert len(json.dumps(result)) < 500


@pytest.mark.parametrize("include_usage", [True, False])
@pytest.mark.parametrize("repair_only", [True, False])
def test_real_pi_ai_sse_and_native_tool_loop_without_network(
    tmp_path: Path, include_usage: bool, repair_only: bool
) -> None:
    """真实 pi-ai provider + pi-agent-core；仅替换 fetch，不走 Python 决策注入器。"""
    preload = tmp_path / "mock-fetch.mjs"
    preload.write_text(
        """
let count = 0;
globalThis.fetch = async (_url, options) => {
  const request = JSON.parse(options.body);
  if (process.env.MOCK_REPAIR==='1' && count===1) {
    if (request.tools?.length) throw new Error('repair offered tools');
  } else if (!request.tools?.[0]?.function?.parameters) throw new Error('missing schema');
  if (request.thinking?.type !== 'disabled') throw new Error('thinking enabled');
  if (request.parallel_tool_calls !== false) throw new Error('parallel enabled');
  count++;
  const delta = count === 1 ? {reasoning_content:'hidden-reasoning', tool_calls:[{
    index:0, id:'native-probe-1', type:'function', function:{name:'probe',arguments:'{"value":3}'}}]} :
    {content:JSON.stringify({type:'final',answer:'结果为3',claims:[],evidence_refs:[]})};
  const chunk = {id:'offline', object:'chat.completion.chunk', created:1, model:'deepseek-flash',
    choices:[{index:0,delta,finish_reason:null}]};
  const finish = {...chunk, choices:[{index:0,delta:{},finish_reason:count===1?'tool_calls':'stop'}]};
  const usage = {choices:[],usage:{prompt_tokens:3,completion_tokens:2,total_tokens:5}};
  const frames = [chunk,finish,...(process.env.MOCK_USAGE==='1'?[usage]:[])];
  return new Response(frames.map(item=>'data: '+JSON.stringify(item)+'\\n\\n').join('')+'data: [DONE]\\n\\n',
    {status:200,headers:{'content-type':'text/event-stream'}});
};
""",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "DEEPSEEK_API_KEY": "offline-test",
        "MOCK_USAGE": "1" if include_usage else "0",
        "MOCK_REPAIR": "1" if repair_only else "0",
    }
    process = subprocess.Popen(
        ["node", "--import", str(preload), str(pi_bridge.RUNTIME_DIR / "runner.mjs")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    events = []

    def send(value: object) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps({**value, "protocol_version": 1}, ensure_ascii=False) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "model": "deepseek-flash",
                "system": "只用工具回答",
                "prompt": "读取3",
                "tools": [
                    {
                        "name": "probe",
                        "description": "离线探针",
                        "arguments": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                    }
                ],
            }
        )
        assert process.stdout is not None
        for line in process.stdout:
            request = json.loads(line)
            if request["event"] == "done":
                break
            assert request["event"] != "failed"
            events.append(request)
            kind = request["event"]
            response = (
                {"protocol_version": 1}
                if kind == "ready"
                else request["payload"]["messages"]
                if kind == "context"
                else (
                    {
                        "max_tokens": 100,
                        "repair_only": repair_only
                        and len([item for item in events if item["event"] == "model_start"]) > 1,
                    }
                    if kind == "model_start"
                    else {"content": '{"value":3}'}
                    if kind == "tool"
                    else {}
                )
            )
            send({"id": request["id"], "ok": True, "result": response})
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    tool_events = [item for item in events if item["event"] == "tool"]
    assert len(tool_events) == 1 and tool_events[0]["payload"]["toolCallId"] == "native-probe-1"
    kinds = [item["event"] for item in events]
    assert kinds[0] == "ready" and kinds.index("tool_check") < kinds.index("tool")
    tool_messages = [item["payload"]["message"] for item in events if item["event"] == "message"]
    assert json.loads(tool_messages[0]["content"][0]["text"]) == {"value": 3}
    model_events = [item["payload"] for item in events if item["event"] == "model_end"]
    assert len(model_events) == 2
    assert all(item["usageKnown"] is include_usage for item in model_events)
    assert all(item["consumed"] == (5 if include_usage else None) for item in model_events)
    assert "hidden-reasoning" not in json.dumps(events)


def test_pi_records_recoverable_native_messages_and_no_background(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "pi")
    result = agent.ask(
        tmp_path,
        "SYNTHETIC",
        InteractiveQuestion(question="用户原话"),
        model_client=agent_model.ScriptedAgentModelClient(
            [
                ({"type": "tool_call", "tool": "list_sources", "arguments": {}}, 3),
                ({"type": "final", "answer": "缺少归档", "gaps": ["无来源"]}, 4),
            ]
        ),
    )
    with storage.open_db(tmp_path) as connection:
        messages = storage.list_agent_messages(connection, result["turn_id"])
    assert [item["role"] for item in messages] == ["user", "assistant", "toolResult", "assistant"]
    assert messages[0]["content"] == "用户原话"
    assert messages[1]["content"][0]["id"] == messages[2]["toolCallId"]
    assert "辅助上下文" not in json.dumps(messages, ensure_ascii=False)


def test_waiting_turn_keeps_frozen_runtime_after_environment_change(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "legacy")
    result = agent.ask(
        tmp_path,
        "SYNTHETIC",
        InteractiveQuestion(question="旧问题"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "ask_user", "question": "哪个期间？"}, 3)]
        ),
    )
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "pi")
    completed = agent.respond(
        tmp_path,
        result["session_id"],
        result["turn_id"],
        InteractiveFollowup(question="2025"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "final", "answer": "旧运行时继续完成。"}, 3)]
        ),
    )
    assert completed["status"] == "completed"
    with storage.open_db(tmp_path) as connection:
        turn = storage.get_agent_turn(connection, result["turn_id"])
        assert json.loads(turn["question_json"])["_agent_runtime"] == "legacy"
        assert storage.list_agent_messages(connection, result["turn_id"]) == []


def test_pi_cannot_execute_finish_or_change_frozen_report(tmp_path: Path, monkeypatch) -> None:
    from tests.test_storage import completed_run, make_root

    root = make_root(tmp_path)
    run_id = completed_run(root, "SYNTHETIC")
    with storage.open_db(root) as connection:
        before = storage.get_run(connection, run_id)
    frozen = {
        path: storage.sha256_file(path)
        for path in (root / "data/stocks").rglob("*")
        if path.is_file()
    }
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "pi")
    with pytest.raises(AgentError, match="未授权"):
        agent.ask(
            root,
            "SYNTHETIC",
            InteractiveQuestion(question="改评分并定稿"),
            model_client=agent_model.ScriptedAgentModelClient(
                [({"type": "tool_call", "tool": "finish", "arguments": {"run_id": run_id}}, 7)]
            ),
        )
    with storage.open_db(root) as connection:
        assert storage.get_run(connection, run_id) == before
    assert all(storage.sha256_file(path) == digest for path, digest in frozen.items())


def test_native_provider_usage_is_kept_in_existing_trace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "pi")

    def run(_config, callback):
        callback("model_start", {"messages": [{"role": "user", "content": "拒绝越权"}]})
        callback(
            "model_end",
            {
                "usageKnown": True,
                "consumed": 5,
                "message": {
                    "role": "assistant",
                    "stopReason": "stop",
                    "usage": {"input": 3, "output": 2},
                    "content": [
                        {"type": "text", "text": '{"type":"final","answer":"不能修改评分。"}'}
                    ],
                },
            },
        )
        callback("settled", {})

    monkeypatch.setattr(pi_bridge, "run", run)
    result = agent.ask(tmp_path, "SYNTHETIC", InteractiveQuestion(question="修改评分"))
    with storage.open_db(tmp_path) as connection:
        spans = [
            dict(row)
            for row in connection.execute("SELECT * FROM trace_spans WHERE span_kind='llm'")
        ]
    assert len(spans) == 1
    attributes = json.loads(spans[0]["attributes_json"])
    assert attributes["runtime"] == "pi" and attributes["total_tokens"] == 5
    assert agent.get_status(tmp_path, result["session_id"])["turns"][0]["used_tokens"] == 5
