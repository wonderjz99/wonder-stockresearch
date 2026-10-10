from __future__ import annotations

import json
from base64 import b64decode
from pathlib import Path

from wonder_stockresearch import observability
from wonder_stockresearch.fixed import research, writer


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def _response() -> dict[str, object]:
    return {
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }


def test_local_trace_is_correlated_and_never_persists_prompt_content(
    tmp_path: Path, monkeypatch: object
) -> None:
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    monkeypatch.setattr(writer, "_request_deepseek", lambda **_kwargs: _response())  # type: ignore[attr-defined]

    with observability.operation(
        root,
        "test.research",
        trace_kind="research",
        run_id=run_id,
        attributes={"prompt_text": "不应保存", "model": "deepseek-flash"},
    ):
        writer._traced_request(
            root,
            api_key="not-a-real-key",
            model="deepseek-flash",
            messages=[
                {"role": "system", "content": "绝不能出现在 trace 里的系统提示词"},
                {"role": "user", "content": "用户私密问题"},
            ],
            temperature=0.2,
            thinking="disabled",
            kind="test",
        )

    trace = observability.read_run_trace(root, run_id)["traces"][0]
    assert trace["status"] == "succeeded"
    assert "prompt_text" not in trace["attributes"]
    span = trace["spans"][0]
    assert span["span_kind"] == "llm"
    assert span["attributes"]["provider"] == "deepseek"
    assert span["attributes"]["llm.model_name"] == "deepseek-flash"
    assert span["attributes"]["llm.token_count.total"] == 20
    assert span["attributes"]["total_tokens"] == 20
    persisted = json.dumps(trace, ensure_ascii=False)
    assert "绝不能出现在" not in persisted
    assert "用户私密问题" not in persisted
    assert "not-a-real-key" not in persisted


def test_content_capture_is_opt_in_and_never_enters_sqlite(
    tmp_path: Path, monkeypatch: object
) -> None:
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    monkeypatch.setenv("WONDER_TRACE_CAPTURE_CONTENT", "full")  # type: ignore[attr-defined]
    monkeypatch.setattr(writer, "_request_deepseek", lambda **_kwargs: _response())  # type: ignore[attr-defined]

    with observability.operation(root, "test.research", trace_kind="research", run_id=run_id):
        writer._traced_request(
            root,
            api_key="not-a-real-key",
            model="deepseek-flash",
            messages=[{"role": "user", "content": "仅可导出到本机 Phoenix 的正文"}],
            temperature=0.2,
            thinking="disabled",
            kind="test",
        )

    trace = observability.read_run_trace(root, run_id)["traces"][0]
    persisted = json.dumps(trace, ensure_ascii=False)
    assert "仅可导出到本机 Phoenix 的正文" not in persisted
    assert observability.trace_content_mode() == "full"


def test_content_capture_prepares_visible_llm_and_tool_io_only_for_export(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("WONDER_TRACE_CAPTURE_CONTENT", "preview")  # type: ignore[attr-defined]
    span = observability.ActiveSpan(root=tmp_path, trace_id="trace", span_id="span")

    observability.export_llm_input(
        span,
        [{"role": "user", "content": "问题 DEEPSEEK_API_KEY=should-not-appear"}],
    )
    observability.export_llm_output(span, "这是模型的可见回答")
    observability.export_tool_io(
        span,
        arguments={"source_id": "source-1"},
        result={"excerpt": "这是工具返回的正文"},
    )

    assert span.attributes == {}
    assert span.export_attributes["llm.input_messages.0.message.role"] == "user"
    assert "[REDACTED]" in str(span.export_attributes["llm.input_messages.0.message.content"])
    assert span.export_attributes["llm.output_messages.0.message.content"] == "这是模型的可见回答"
    assert span.export_attributes["input.value"] == '{"source_id": "source-1"}'
    assert "工具返回的正文" in str(span.export_attributes["output.value"])
    assert "langfuse.observation.input" in span.export_attributes
    assert "langfuse.observation.output" in span.export_attributes


def test_langfuse_exporter_configuration_uses_project_keys_without_exposing_them(
    monkeypatch: object,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")  # type: ignore[attr-defined]
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")  # type: ignore[attr-defined]
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com/")  # type: ignore[attr-defined]

    options = observability._langfuse_exporter_options()  # type: ignore[attr-defined]

    assert options is not None
    endpoint, headers = options
    assert endpoint == "https://us.cloud.langfuse.com/api/public/otel/v1/traces"
    assert headers["x-langfuse-ingestion-version"] == "4"
    encoded = headers["Authorization"].removeprefix("Basic ")
    assert b64decode(encoded).decode("utf-8") == "pk-lf-test:sk-lf-test"


def test_trace_read_cli_returns_run_trace(tmp_path: Path, capsys: object) -> None:
    from wonder_stockresearch.cli import main

    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    with observability.operation(root, "test.research", trace_kind="research", run_id=run_id):
        pass

    assert main(["--root", str(root), "trace-read", run_id]) == 0
    payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert payload["ok"] is True
    assert payload["data"]["run_id"] == run_id
    assert payload["data"]["traces"][0]["name"] == "test.research"
