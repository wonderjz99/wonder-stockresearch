"""Pi JSONL 协议的离线契约与失败边界，不使用模型或 OpenD。"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.interactive.pi import pi_bridge, pi_protocol


def config(**updates) -> dict:
    return {"model": "offline", "system": "受控规则", "tools": [], "prompt": "问题", **updates}


def frame(event: str, payload: dict, *, request_id: int = 1, **updates) -> dict:
    return {
        "protocol_version": 1,
        "id": request_id,
        "event": event,
        "payload": payload,
        **updates,
    }


def ready() -> pi_protocol.ProtocolSession:
    session = pi_protocol.ProtocolSession()
    session.accept(frame("ready", {"protocol_version": 1}))
    return session


def test_initial_version_defaults_only_when_omitted() -> None:
    assert pi_protocol.validate_config(config())["protocol_version"] == 1
    for value in (2, True, "1", None):
        with pytest.raises(AgentError) as error:
            pi_protocol.validate_config(config(protocol_version=value))
        assert error.value.code == "pi_protocol_error"


def test_incompatible_config_is_rejected_before_starting_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("不兼容的初始化配置不能启动 Node")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(AgentError) as error:
        pi_bridge.run(config(protocol_version=2), lambda *_: {})
    assert error.value.code == "pi_protocol_error"


@pytest.mark.parametrize("request_id", [True, 0, -1, "2", 2**53])
def test_request_ids_are_positive_safe_integers(request_id: object) -> None:
    with pytest.raises(AgentError):
        ready().accept(frame("settled", {}, request_id=request_id))


def test_handshake_is_required_and_cannot_repeat() -> None:
    with pytest.raises(AgentError, match="握手"):
        pi_protocol.ProtocolSession().accept(frame("model_start", {"messages": []}))
    session = ready()
    session.accept(frame("lifecycle", {"type": "agent_start"}, request_id=2))
    with pytest.raises(AgentError, match="严格递增"):
        session.accept(frame("settled", {}, request_id=2))
    with pytest.raises(AgentError, match="重复版本握手"):
        session.accept(frame("ready", {"protocol_version": 1}, request_id=3))


@pytest.mark.parametrize(
    "mutation",
    [
        {"protocol_version": 2},
        {"protocol_version": True},
        {"event": "unknown"},
        {"extra": "secret"},
        {"payload": {"type": "agent_start", "reasoning": "hidden"}},
    ],
)
def test_frames_reject_unknown_fields_events_and_versions(mutation: dict) -> None:
    request = frame("lifecycle", {"type": "agent_start"}, request_id=2)
    request.update(mutation)
    with pytest.raises(AgentError) as error:
        ready().accept(request)
    assert error.value.code == "pi_protocol_error"
    assert "hidden" not in str(error.value) and "secret" not in str(error.value)


@pytest.mark.parametrize(
    "message",
    [
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "hidden"}]},
        {"role": "assistant", "content": [], "diagnostics": "secret"},
        {"role": "assistant", "content": [{"type": "text", "text": "可见", "signature": "secret"}]},
        {"role": "system", "content": "不能从历史修改系统规则"},
    ],
)
def test_history_rejects_reasoning_and_unapproved_message_fields(message: dict) -> None:
    with pytest.raises(AgentError):
        pi_protocol.validate_config(config(history=[message]))


def test_partial_visible_usage_does_not_require_provider_metadata() -> None:
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": "可见答案"}],
        "usage": {"input": 3, "output": 2},
    }
    original = copy.deepcopy(message)
    request = ready().accept(
        frame(
            "model_end",
            {
                "message": message,
                "usageKnown": True,
                "consumed": 5,
            },
            request_id=2,
        )
    )
    assert request.payload["message"] == original
    assert message == original


@pytest.mark.parametrize("consumed", [True, -1, "5", 1.5, 2**53])
def test_usage_receipts_reject_non_integer_or_unsafe_tokens(consumed: object) -> None:
    with pytest.raises(AgentError):
        ready().accept(
            frame(
                "model_end",
                {
                    "message": {"role": "assistant", "content": []},
                    "usageKnown": True,
                    "consumed": consumed,
                },
                request_id=2,
            )
        )


@pytest.mark.parametrize(
    "usage",
    [
        {"input": True},
        {"output": -1},
        {"totalTokens": float("nan")},
        {"cost": {"total": float("inf")}},
        {"raw_response": "secret"},
    ],
)
def test_visible_usage_rejects_unsafe_numbers_and_raw_provider_data(usage: dict) -> None:
    with pytest.raises(AgentError):
        pi_protocol.validate_config(
            config(
                history=[
                    {
                        "role": "assistant",
                        "content": [],
                        "usage": usage,
                    }
                ]
            )
        )


@pytest.mark.parametrize(
    "event,result",
    [
        ("context", [{"role": "assistant", "content": [], "thinking": "hidden"}]),
        ("model_start", {"max_tokens": True}),
        ("model_start", {"max_tokens": 0}),
        ("model_start", {"max_tokens": 100, "repair_only": "false"}),
        ("tool", {"content": {"raw": "wrong shape"}}),
        ("scripted_model", {"decision": {}, "consumed": True}),
        ("settled", {"ignored": "unknown"}),
    ],
)
def test_callback_results_are_checked_before_returning_to_node(event: str, result: object) -> None:
    with pytest.raises(AgentError):
        pi_protocol.validate_result(event, result)


def test_done_and_failed_frames_have_no_rpc_fields() -> None:
    session = ready()
    assert isinstance(
        session.accept({"protocol_version": 1, "event": "done"}), pi_protocol.DoneFrame
    )
    with pytest.raises(AgentError):
        ready().accept({"protocol_version": 1, "event": "done", "id": 2})
    with pytest.raises(AgentError):
        ready().accept({"protocol_version": 1, "event": "failed", "code": "raw_provider_error"})


@pytest.mark.skipif(shutil.which("node") is None, reason="需要本机 Node，完全离线")
def test_fatal_callback_exception_terminates_node_without_becoming_a_tool_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "node_modules/@earendil-works/pi-agent-core").mkdir(parents=True)
    (runtime / "runner.mjs").write_text(
        """
import { createInterface } from 'node:readline';
const lines = createInterface({input: process.stdin});
let step = 0;
const send = value => process.stdout.write(JSON.stringify({protocol_version:1,...value})+'\\n');
lines.on('line', line => {
  JSON.parse(line);
  if (++step === 1) send({id:1,event:'ready',payload:{protocol_version:1}});
  else if (step === 2) send({id:2,event:'model_start',payload:{messages:[]}});
  else { send({event:'done'}); lines.close(); }
});
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(pi_bridge, "RUNTIME_DIR", runtime)
    processes = []
    original = subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    events = []

    def callback(kind, payload):
        events.append(kind)
        raise AgentError("agent_budget_exhausted", "没有剩余预算")

    with pytest.raises(AgentError) as error:
        pi_bridge.run(config(), callback)
    assert error.value.code == "agent_budget_exhausted"
    assert events == ["model_start"]  # ready 不进入业务 callback。
    assert len(processes) == 1 and processes[0].poll() is not None


@pytest.mark.skipif(shutil.which("node") is None, reason="需要本机 Node，完全离线")
@pytest.mark.parametrize("failure", ["initialization", "reply"])
def test_node_protocol_failure_uses_a_valid_terminal_frame_without_network(
    tmp_path: Path,
    failure: str,
) -> None:
    preload = tmp_path / "block-fetch.mjs"
    preload.write_text(
        "globalThis.fetch = async () => { process.stderr.write('unexpected fetch'); "
        "throw new Error('network forbidden'); };",
        encoding="utf-8",
    )
    messages = [config(protocol_version=2 if failure == "initialization" else 1)]
    if failure == "reply":
        messages.append(
            {"protocol_version": 2, "id": 1, "ok": True, "result": {"protocol_version": 1}}
        )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"NODE_OPTIONS", "NODE_PATH", "NODE_DEBUG"}
    }
    completed = subprocess.run(
        ["node", "--import", str(preload), str(pi_bridge.RUNTIME_DIR / "runner.mjs")],
        input="".join(json.dumps(message) + "\n" for message in messages),
        capture_output=True,
        text=True,
        env={**env, "DEEPSEEK_API_KEY": "offline-test"},
        timeout=10,
        check=False,
    )
    assert completed.returncode == 1
    assert "unexpected fetch" not in completed.stderr
    protocol = pi_protocol.ProtocolSession()
    frames = [protocol.accept(json.loads(line)) for line in completed.stdout.splitlines()]
    assert len(frames) == (1 if failure == "initialization" else 2)
    assert isinstance(frames[-1], pi_protocol.FailedFrame)
    assert frames[-1].code == "pi_runtime_failed"
