from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import research, storage, writer
from wonder_stockresearch.cli import main
from wonder_stockresearch.models import ResearchPacket, SourceRequest
from wonder_stockresearch.writer import WriterError

API_KEY = "test-key-123"


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def packet_for(ticker: str = "SYNTHETIC") -> ResearchPacket:
    data = copy_synthetic_packet()
    data["draft"]["ticker"] = ticker
    for source in data["sources"]:
        source["ticker"] = ticker
    return ResearchPacket.model_validate(data)


def seeded_run(root: Path) -> str:
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet_for())
    return run_id


def plain_run(root: Path) -> str:
    return str(research.start_run(root, "SYNTHETIC")["run_id"])


def fake_response(content: str, finish_reason: str = "stop") -> dict[str, object]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
    }


def response_sequence(*contents: str):
    responses = iter(contents)

    def request(**kwargs: object) -> dict[str, object]:
        return fake_response(next(responses))

    return request


def step_for(root: Path, run_id: str, step_key: str) -> dict[str, object]:
    return next(
        step for step in research.get_status(root, run_id)["steps"] if step["step_key"] == step_key
    )


def test_writer_prompt_defines_question_scope_and_prevents_q03_q04_substitution() -> None:
    prompt = writer._system_rules()

    assert "Q03: 业务多依赖资本投入？资本开支占收入的实际口径与覆盖期是什么？" in prompt
    assert "Q04: 技术替代和产品变化有多快，会如何影响竞争地位？" in prompt
    assert "利润率、ROE、净利润或 EPS 不能替代资本开支分析" in prompt
    assert "EPS、利润率或估值指标本身不能回答技术替代问题" in prompt
    assert "M1 只在存在可核验的单位成本或成本结构优势证据时才给非 null 分数" in prompt


def test_write_draft_success_saves_validates_and_records_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving without validating, auto-finishing, or leaking the key must fail this test."""
    root = make_root(tmp_path)
    run_id = seeded_run(root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", API_KEY)
    monkeypatch.setattr(
        writer,
        "_request_deepseek",
        lambda **kwargs: fake_response(json.dumps(copy_synthetic_packet()["draft"])),
    )

    data, issues = writer.write_draft(root, run_id, strategy="one-shot")

    assert issues == []
    assert data["saved"] is True
    assert data["provider"] == "deepseek"
    assert data["model"] == writer.DEFAULT_MODEL
    assert data["thinking"] == "disabled"
    assert data["temperature"] == writer.DEFAULT_TEMPERATURE
    assert data["usage"] == {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}
    assert data["input_hash"]

    status = research.get_status(root, run_id)
    assert status["status"] == "draft"  # writer must not finish
    assert status["answer_count"] == 24
    assert research.validate_run(root, run_id) == []

    step = step_for(root, run_id, "write_draft")
    assert step["status"] == "succeeded"
    assert step["provider"] == "deepseek"
    assert step["model"] == writer.DEFAULT_MODEL
    assert step["input_hash"]
    assert step["output_ref"] == f"draft:{run_id}"
    metadata = json.loads(str(step["metadata_json"]))
    assert metadata["template_version"] == writer.PROMPT_TEMPLATE_VERSION
    assert metadata["thinking"] == "disabled"
    assert metadata["temperature"] == writer.DEFAULT_TEMPERATURE
    assert metadata["usage"]["total_tokens"] == 33
    assert metadata["validation_issue_count"] == 0
    assert API_KEY not in str(step["metadata_json"])


def test_request_payload_disables_thinking_and_applies_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sending thinking-enabled defaults or dropping temperature must fail this test."""
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "{}"}}]}

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        return FakeResponse()

    monkeypatch.setattr(writer.requests, "post", fake_post)

    writer._request_deepseek(api_key="sk-x", model="deepseek-flash", messages=[])
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["temperature"] == writer.DEFAULT_TEMPERATURE
    assert payload["response_format"] == {"type": "json_object"}
    assert "sk-x" not in str(payload)

    writer._request_deepseek(
        api_key="sk-x", model="deepseek-flash", messages=[], temperature=0.0, thinking="enabled"
    )
    assert isinstance(captured["payload"], dict)
    assert captured["payload"]["temperature"] == 0.0
    assert captured["payload"]["thinking"] == {"type": "enabled"}

    monkeypatch.setenv("WONDER_DEEPSEEK_TEMPERATURE", "0.7")
    writer._request_deepseek(api_key="sk-x", model="deepseek-flash", messages=[])
    assert captured["payload"]["temperature"] == 0.7


def test_write_draft_missing_api_key_stops_before_calling_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling the model without a configured key must fail this test."""
    root = make_root(tmp_path)
    run_id = plain_run(root)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(WriterError) as exc_info:
        writer.write_draft(root, run_id, strategy="one-shot")

    assert exc_info.value.code == "missing_api_key"
    assert exc_info.value.exit_code == 2
    assert research.get_status(root, run_id)["steps"] == []


def test_write_draft_request_failure_records_failed_step_and_no_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swallowing a network failure or saving a partial draft must fail this test."""
    root = make_root(tmp_path)
    run_id = plain_run(root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", API_KEY)

    def boom(**kwargs: object) -> dict[str, object]:
        raise WriterError("request_failed", "DeepSeek 请求失败：Timeout")

    monkeypatch.setattr(writer, "_request_deepseek", boom)

    with pytest.raises(WriterError) as exc_info:
        writer.write_draft(root, run_id, strategy="one-shot")
    assert exc_info.value.code == "request_failed"

    step = step_for(root, run_id, "write_draft")
    assert step["status"] == "failed"
    assert "Timeout" in str(step["error_json"])
    with storage.open_db(root) as connection:
        assert storage.get_run(connection, run_id)["draft_json"] is None


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("这不是 JSON", "invalid_json"),
        (json.dumps({"answers": "不是列表"}), "invalid_schema"),
        ("", "empty_content"),
    ],
)
def test_write_draft_rejects_bad_model_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str, code: str
) -> None:
    """Accepting unparsable or schema-invalid model output must fail this test."""
    root = make_root(tmp_path)
    run_id = plain_run(root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", API_KEY)
    monkeypatch.setattr(writer, "_request_deepseek", lambda **kwargs: fake_response(content))

    with pytest.raises(WriterError) as exc_info:
        writer.write_draft(root, run_id, strategy="one-shot")
    assert exc_info.value.code == code
    assert step_for(root, run_id, "write_draft")["status"] == "failed"


@pytest.mark.parametrize(
    "bad_gap",
    [
        "缺少分业务收入拆分",
        ["缺少分业务收入拆分"],
        {"missing": "缺少分业务收入拆分"},
    ],
    ids=["string", "array", "incomplete-object"],
)
def test_write_draft_repairs_gap_schema_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_gap: object
) -> None:
    root = make_root(tmp_path)
    run_id = seeded_run(root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", API_KEY)
    invalid = copy_synthetic_packet()["draft"]
    invalid["answers"][2]["gap"] = bad_gap
    valid = copy_synthetic_packet()["draft"]
    monkeypatch.setattr(
        writer,
        "_request_deepseek",
        response_sequence(json.dumps(invalid), json.dumps(valid)),
    )

    data, issues = writer.write_draft(root, run_id, strategy="one-shot")

    assert issues == []
    assert data["saved"] is True
    step = step_for(root, run_id, "write_draft")
    metadata = json.loads(str(step["metadata_json"]))
    assert metadata["repair_attempted"] is True
    assert len(metadata["model_attempts"]) == 2
    assert metadata["model_attempts"][1]["kind"] == "schema_repair"
    assert metadata["initial_validation"]["status"] == "failed"
    assert metadata["final_validation"]["status"] == "passed"
    assert metadata["validation_issue_count"] == 0


def test_write_draft_repair_failure_keeps_strict_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    run_id = plain_run(root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", API_KEY)
    invalid = copy_synthetic_packet()["draft"]
    invalid["answers"][2]["gap"] = "缺少分业务收入拆分"
    monkeypatch.setattr(
        writer,
        "_request_deepseek",
        response_sequence(json.dumps(invalid), json.dumps(invalid)),
    )

    with pytest.raises(WriterError) as exc_info:
        writer.write_draft(root, run_id, strategy="one-shot")

    assert exc_info.value.code == "invalid_schema"
    assert "自动修复后仍失败" in exc_info.value.message
    status = research.get_status(root, run_id)
    assert status["status"] == "draft"
    with storage.open_db(root) as connection:
        assert storage.get_run(connection, run_id)["draft_json"] is None
    metadata = json.loads(str(step_for(root, run_id, "write_draft")["metadata_json"]))
    assert metadata["repair_attempted"] is True
    assert metadata["final_validation"]["status"] == "failed"
    assert "缺少分业务收入拆分" not in str(metadata)
    assert API_KEY not in str(metadata)


def test_write_draft_rejects_repair_that_changes_non_gap_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    run_id = seeded_run(root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", API_KEY)
    invalid = copy_synthetic_packet()["draft"]
    invalid["answers"][2]["gap"] = "缺少分业务收入拆分"
    repaired_but_changed = copy_synthetic_packet()["draft"]
    repaired_but_changed["answers"][0]["statements"][0]["text"] = "被意外改写的陈述"
    monkeypatch.setattr(
        writer,
        "_request_deepseek",
        response_sequence(json.dumps(invalid), json.dumps(repaired_but_changed)),
    )

    with pytest.raises(WriterError) as exc_info:
        writer.write_draft(root, run_id, strategy="one-shot")

    assert exc_info.value.code == "invalid_schema"
    assert "改变了 gap 以外的研究内容" in exc_info.value.message
    with storage.open_db(root) as connection:
        stored_draft = storage.get_run(connection, run_id)["draft_json"]
        assert stored_draft is not None
        assert "被意外改写的陈述" not in stored_draft


def test_write_draft_unknown_source_reference_is_saved_for_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Letting an invented source_id pass validation must fail this test."""
    root = make_root(tmp_path)
    run_id = seeded_run(root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", API_KEY)
    payload = copy_synthetic_packet()["draft"]
    payload["answers"][0]["statements"][0]["evidence"][0]["source_id"] = (
        "99999999-9999-4999-8999-999999999999"
    )
    monkeypatch.setattr(writer, "_request_deepseek", lambda **kwargs: fake_response(json.dumps(payload)))

    data, issues = writer.write_draft(root, run_id, strategy="one-shot")

    assert "unknown_source" in {issue.code for issue in issues}
    assert data["saved"] is True
    assert research.get_status(root, run_id)["status"] == "draft"
    assert "unknown_source" in {issue.code for issue in research.validate_run(root, run_id)}
    assert step_for(root, run_id, "write_draft")["status"] == "succeeded"


def test_request_deepseek_http_error_does_not_leak_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returning the Authorization header or treating HTTP 500 as success must fail here."""

    class FakeResponse:
        status_code = 500

    monkeypatch.setattr(writer.requests, "post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(WriterError) as exc_info:
        writer._request_deepseek(api_key="sk-secret-value", model="deepseek-flash", messages=[])
    assert exc_info.value.code == "http_error"
    assert "sk-secret-value" not in exc_info.value.message

    class UnauthorizedResponse:
        status_code = 401

    monkeypatch.setattr(writer.requests, "post", lambda *args, **kwargs: UnauthorizedResponse())
    with pytest.raises(WriterError) as unauthorized:
        writer._request_deepseek(api_key="sk-secret-value", model="deepseek-flash", messages=[])
    assert unauthorized.value.code == "unauthorized"


def test_build_messages_includes_archived_excerpt_and_source_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping archived text or source ids from the context must fail this test."""
    root = make_root(tmp_path)
    run_id = plain_run(root)
    document = tmp_path / "annual.txt"
    document.write_text("营业收入为 529,559 百万元。\n", encoding="utf-8")
    research.source_add(
        root,
        run_id,
        SourceRequest(
            kind="primary",
            title="年度报告",
            file=str(document),
            published_at=datetime(2026, 9, 1, tzinfo=UTC),
            publication_precision="datetime",
        ),
    )
    with storage.open_db(root) as connection:
        run = storage.get_run(connection, run_id)
        rows = storage.get_run_sources(connection, run_id)
    from wonder_stockresearch.models import Source

    sources = [Source.model_validate_json(row["source_json"]) for row in rows]

    system, user = writer._build_messages(root, run, sources)

    assert "529,559" in user
    assert str(rows[0]["id"]) in user
    assert "json" in system.lower()


def test_cli_write_draft_success_and_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    """Breaking the write-draft CLI contract must fail this test."""
    root = make_root(tmp_path)
    run_id = seeded_run(root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", API_KEY)
    monkeypatch.setattr(
        writer,
        "_request_deepseek",
        lambda **kwargs: fake_response(json.dumps(copy_synthetic_packet()["draft"])),
    )

    assert main(["--root", str(root), "write-draft", run_id, "--strategy", "one-shot"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["model"] == writer.DEFAULT_MODEL

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    other_run = plain_run(root)
    assert main(["--root", str(root), "write-draft", other_run, "--strategy", "one-shot"]) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["ok"] is False
    assert failed["issues"][0]["code"] == "missing_api_key"
