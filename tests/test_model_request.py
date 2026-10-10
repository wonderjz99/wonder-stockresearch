from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wonder_stockresearch import agent, model_request, observability, storage
from wonder_stockresearch.fixed import research, writer
from wonder_stockresearch.interactive import agent_model
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import InteractiveQuestion


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def response(content: str = "{}", *, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
    }
    if usage is not None:
        result["usage"] = usage
    return result


@pytest.mark.parametrize(
    "status,code", [(401, "unauthorized"), (403, "unauthorized"), (500, "http_error")]
)
def test_http_error_never_echoes_provider_body(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    class FakeResponse:
        status_code = status

        def json(self) -> dict[str, Any]:
            return {"error": {"message": "Bearer offline-sensitive-token private-prompt"}}

    calls: list[object] = []

    def post(*args: object, **kwargs: object) -> FakeResponse:
        calls.append(kwargs)
        return FakeResponse()

    monkeypatch.setattr(model_request.requests, "post", post)
    with pytest.raises(model_request.ModelRequestError) as exc:
        model_request.request_deepseek(
            api_key="offline-sensitive-token", model="fixture", messages=[]
        )
    assert exc.value.code == code
    assert "offline-sensitive-token" not in str(exc.value)
    assert "private-prompt" not in str(exc.value)
    assert len(calls) == 1


def test_public_request_keeps_timeout_config_and_no_automatic_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> None:
        captured.append({"url": url, **kwargs})
        raise model_request.requests.Timeout("sensitive-placeholder")

    monkeypatch.setenv("WONDER_DEEPSEEK_BASE_URL", "https://configured.example/")
    monkeypatch.setenv("WONDER_DEEPSEEK_MAX_TOKENS", "2048")
    monkeypatch.setattr(model_request.requests, "post", post)
    with pytest.raises(model_request.ModelRequestError) as exc:
        model_request.request_deepseek(api_key="offline-placeholder", model="fixture", messages=[])
    assert exc.value.code == "request_failed"
    assert "sensitive-placeholder" not in str(exc.value)
    assert len(captured) == 1
    assert captured[0]["url"] == "https://configured.example/chat/completions"
    assert captured[0]["timeout"] == (10, 300)
    assert captured[0]["json"]["max_tokens"] == 2048
    assert captured[0]["json"]["thinking"] == {"type": "disabled"}


def test_shared_trace_exports_only_visible_output_and_keeps_local_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    result = response('{"visible":"public-answer"}', usage={"total_tokens": 23})
    result["choices"][0]["message"]["reasoning_content"] = "hidden-reasoning-placeholder"
    outputs: list[str] = []
    monkeypatch.setattr(model_request, "request_deepseek", lambda **kwargs: result)
    monkeypatch.setattr(
        observability, "export_llm_output", lambda span, content: outputs.append(content)
    )

    with observability.operation(root, "shared.request", trace_kind="research", run_id=run_id):
        model_request.traced_request(
            root,
            api_key="offline-key-placeholder",
            model="fixture",
            messages=[{"role": "user", "content": "private-question-placeholder"}],
            temperature=0.2,
            thinking="disabled",
            kind="router",
        )

    trace = observability.read_run_trace(root, run_id)["traces"][0]
    span = trace["spans"][0]
    assert span["attributes"]["call_kind"] == "router"
    assert span["attributes"]["llm.token_count.total"] == 23
    assert outputs == ['{"visible":"public-answer"}']
    persisted = json.dumps(trace, ensure_ascii=False)
    for private in (
        "hidden-reasoning-placeholder",
        "offline-key-placeholder",
        "private-question-placeholder",
        "public-answer",
    ):
        assert private not in persisted


def test_visible_content_never_falls_back_to_hidden_reasoning() -> None:
    result = {"choices": [{"message": {"content": "", "reasoning_content": "hidden"}}]}
    with pytest.raises(model_request.ModelRequestError) as exc:
        model_request.extract_content(result)
    assert exc.value.code == "empty_content"


@pytest.mark.parametrize(
    "content,code", [("broken json", "invalid_decision"), ("", "empty_content")]
)
def test_legacy_decision_retains_usage_when_output_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str, code: str
) -> None:
    root = make_root(tmp_path)
    captured: dict[str, Any] = {}

    def request(*args: object, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return response(content, usage={"total_tokens": 23})

    monkeypatch.setattr(model_request, "api_key", lambda: "offline-placeholder")
    monkeypatch.setattr(model_request, "traced_request", request)
    reservation = agent_model.remaining_request_tokens.set(100)
    try:
        with pytest.raises(AgentError) as exc:
            agent_model.deepseek_decision(root, [{"role": "user", "content": "hello"}], "fixture")
    finally:
        agent_model.remaining_request_tokens.reset(reservation)
    assert exc.value.code == code
    assert exc.value.consumed_tokens == 23
    assert captured["max_tokens"] == 66


def test_legacy_decision_keeps_unknown_usage_and_stops_before_unaffordable_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path)
    calls: list[object] = []

    def request(*args: object, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return response()

    monkeypatch.setattr(model_request, "api_key", lambda: "offline-placeholder")
    monkeypatch.setattr(model_request, "traced_request", request)
    with pytest.raises(AgentError) as unknown_usage:
        agent_model.deepseek_decision(root, [{"role": "user", "content": "hello"}], "fixture")
    assert unknown_usage.value.code == "model_usage_unavailable"
    assert unknown_usage.value.consumed_tokens is None
    assert len(calls) == 1

    reservation = agent_model.remaining_request_tokens.set(10)
    try:
        with pytest.raises(AgentError) as exhausted:
            agent_model.deepseek_decision(root, [{"role": "user", "content": "hello"}], "fixture")
    finally:
        agent_model.remaining_request_tokens.reset(reservation)
    assert exhausted.value.code == "agent_budget_exhausted"
    assert exhausted.value.consumed_tokens == 0
    assert len(calls) == 1


def test_writer_translates_public_errors_and_preserves_its_request_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(writer.WriterError) as missing:
        writer._api_key()
    assert missing.value.code == "missing_api_key"
    assert missing.value.exit_code == 2

    calls: list[object] = []

    def legacy_request(**kwargs: object) -> dict[str, Any]:
        calls.append(kwargs)
        return response(usage={"total_tokens": 17})

    monkeypatch.setattr(writer, "_request_deepseek", legacy_request)
    result = writer._traced_request(
        root,
        api_key="offline-placeholder",
        model="fixture",
        messages=[],
        temperature=0.2,
        thinking="disabled",
        kind="writer",
        max_tokens=512,
    )
    assert writer._usage_summary(result) == {"total_tokens": 17}
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 512


@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, "23", None])
def test_invalid_total_usage_preserves_attempt_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: object,
) -> None:
    root = make_root(tmp_path)
    calls: list[object] = []

    def request(**kwargs: object) -> dict[str, Any]:
        calls.append(kwargs)
        return response(
            '{"type":"final","answer":"fixture"}',
            usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": invalid},
        )

    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "legacy")
    monkeypatch.setattr(model_request, "api_key", lambda: "offline-placeholder")
    monkeypatch.setattr(model_request, "request_deepseek", request)
    question = InteractiveQuestion(question="用量格式校验", token_budget=10000, allow_network=False)
    with pytest.raises(AgentError) as exc:
        agent.ask(root, "SYNTHETIC", question, tool_names=("list_memos",))
    assert exc.value.code == "model_usage_unavailable"
    assert exc.value.consumed_tokens is None
    assert len(calls) == 1
    with storage.open_db(root) as connection:
        session_id = str(connection.execute("SELECT id FROM agent_sessions").fetchone()["id"])
        assert connection.execute("SELECT COUNT(*) FROM interactive_reports").fetchone()[0] == 0
    status = agent.get_status(root, session_id)
    turn = status["turns"][0]
    assert status["status"] == turn["status"] == "failed"
    assert status["used_tokens"] == turn["used_tokens"] == question.token_budget
    assert turn["attempts"][0]["used_tokens"] == question.token_budget


@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, "23", None])
def test_writer_does_not_aggregate_invalid_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: object,
) -> None:
    root = make_root(tmp_path)
    result = response(
        usage={
            key: invalid
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            )
        }
    )
    assert model_request.usage_summary(result) is None
    assert writer._usage_summary(result) is None
    tracker = {
        "calls": 0,
        "attempts": [],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 20,
        },
    }
    monkeypatch.setattr(writer, "_traced_request", lambda *args, **kwargs: result)
    writer._tracked_request(
        root,
        tracker,
        api_key="offline-placeholder",
        model="fixture",
        messages=[],
        temperature=0.2,
        thinking="disabled",
        kind="writer",
    )
    assert tracker["calls"] == 1
    assert tracker["usage"] == {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
    assert tracker["attempts"][0]["usage"] is None


def test_usage_summary_keeps_zero_without_inventing_total() -> None:
    assert model_request.usage_summary(
        response(
            usage={
                "prompt_tokens": True,
                "completion_tokens": -1,
                "total_tokens": 0,
            }
        )
    ) == {"total_tokens": 0}
    assert model_request.usage_summary(
        response(
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 8,
            }
        )
    ) == {"prompt_tokens": 12, "completion_tokens": 8}
