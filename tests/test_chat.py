from __future__ import annotations

import json
from pathlib import Path

from wonder_stockresearch import chat, cli, progress, router, storage, symbols
from wonder_stockresearch.fixed import research
from wonder_stockresearch.models import RoutePlan, SymbolCandidate


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    skills = root / "skills" / "company-research"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: company-research\ndescription: 固定公司研究\n---\n\n固定流程说明。\n",
        encoding="utf-8",
    )
    return root


def test_router_validates_structured_deepseek_route(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "intent": "fixed_research",
                            "company_name": "中国移动",
                            "ticker": "HK.00941",
                            "question": None,
                            "focus": "general",
                            "confidence": 0.94,
                            "reason": "用户要求研究公司",
                            "requires_confirmation": True,
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(router.model_request, "request_deepseek", lambda **kwargs: payload)

    plan = router.route(root, "帮我弄一份中国移动报告")

    assert plan.intent == "fixed_research"
    assert plan.ticker == "HK.00941"
    assert plan.requires_confirmation is True


def test_router_treats_null_focus_as_general(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "intent": "fixed_research",
                            "company_name": "中国联通",
                            "ticker": "HK.00762",
                            "question": None,
                            "focus": None,
                            "confidence": 0.9,
                            "reason": "用户要求研究中国联通",
                            "requires_confirmation": True,
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(router.model_request, "request_deepseek", lambda **kwargs: payload)

    plan = router.route(root, "帮我研究一下中国联通")

    assert plan.ticker == "HK.00762"
    assert plan.focus == "general"


def test_chat_requires_confirmation_then_runs_to_validate(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    plan = RoutePlan(
        intent="fixed_research",
        company_name="教学公司",
        ticker="SYNTHETIC",
        confidence=0.9,
        reason="请求正式报告",
    )
    monkeypatch.setattr(chat.router, "route", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        chat.research,
        "collect",
        lambda *args, **kwargs: ({"succeeded": 1, "failed": 0, "blocked": 0}, []),
    )
    monkeypatch.setattr(chat.writer, "write_draft", lambda *args, **kwargs: ({}, []))
    monkeypatch.setattr(chat.research, "validate_run", lambda *args, **kwargs: [])
    finished: list[str] = []
    monkeypatch.setattr(
        chat.research,
        "finish_run",
        lambda root, run_id: finished.append(run_id) or {"run_id": run_id, "status": "completed"},
    )

    first = chat.run_once(root, "帮我弄一份教学公司的报告")
    assert first["status"] == "awaiting_confirmation"
    session_id = str(first["session_id"])

    started = chat.run_once(root, "确认开始", session_id=session_id)
    assert started["status"] == "awaiting_finish"
    run_id = str(started["run_id"])
    assert finished == []

    completed = chat.run_once(root, "确认定稿", session_id=session_id)
    assert completed["status"] == "completed"
    assert finished == [run_id]

    with storage.open_db(root) as connection:
        row = storage.get_chat_session(connection, session_id)
    assert row is not None
    assert row["status"] == "completed"
    assert row["pending_json"] is None


def test_fixed_chat_validation_failure_includes_issue_details_and_run_id(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    plan = RoutePlan(
        intent="fixed_research",
        company_name="教学公司",
        ticker="SYNTHETIC",
        confidence=0.9,
        reason="请求正式报告",
    )
    issues = [
        research.ValidationIssue(
            "missing_authoritative_support",
            "draft.answers[0].statements[0]",
            "需要 primary 或 provider 的 supports 引用",
        )
    ]
    monkeypatch.setattr(
        chat.research,
        "start_run",
        lambda *args, **kwargs: {"run_id": "run-failed", "status": "draft"},
    )
    monkeypatch.setattr(
        chat.research,
        "collect",
        lambda *args, **kwargs: ({"succeeded": 1, "failed": 0, "blocked": 0}, []),
    )
    monkeypatch.setattr(chat.writer, "write_draft", lambda *args, **kwargs: ({}, []))
    monkeypatch.setattr(chat.research, "validate_run", lambda *args, **kwargs: issues)
    monkeypatch.setattr(chat, "_update", lambda *args, **kwargs: None)

    result = chat._execute_fixed(
        root,
        "session-1",
        {"history_json": "[]"},
        plan,
        model=None,
        on_progress=None,
    )

    assert result["status"] == "validation_failed"
    assert result["run_id"] == "run-failed"
    assert "未通过证据校验" in result["message"]
    assert result["issues"][0]["location"] == "draft.answers[0].statements[0]"


def test_chat_validation_failure_shows_human_readable_diagnostics_in_tty(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    data = {
        "session_id": "chat-1",
        "run_id": "run-failed",
        "status": "validation_failed",
        "message": "草稿已生成但未通过证据校验；尚未生成或冻结正式报告。",
        "issues": [
            {
                "code": "missing_authoritative_support",
                "location": "draft.answers[0].statements[0]",
                "message": "需要 primary 或 provider 的 supports 引用",
                "severity": "error",
            }
        ],
    }

    class TerminalOutput:
        def __init__(self) -> None:
            self.value = ""

        def isatty(self) -> bool:
            return True

        def write(self, value: str) -> int:
            self.value += value
            return len(value)

        def flush(self) -> None:
            return None

    terminal = TerminalOutput()
    monkeypatch.setattr(cli.sys, "stdout", terminal)

    cli._print_chat_response(root, data)

    assert "未通过证据校验" in terminal.value
    assert "run_id: run-failed" in terminal.value
    assert "draft.answers[0].statements[0]" in terminal.value
    assert "missing_authoritative_support" in terminal.value
    assert "需要 primary 或 provider 的 supports 引用" in terminal.value
    assert '"issues"' not in terminal.value


def test_chat_text_validation_failure_preserves_json_error_contract(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = make_root(tmp_path)
    failure = {
        "session_id": "chat-1",
        "run_id": "run-failed",
        "status": "validation_failed",
        "message": "草稿已生成但未通过证据校验。",
        "issues": [
            {
                "code": "missing_authoritative_support",
                "location": "draft.answers[0].statements[0]",
                "message": "需要 primary 或 provider 的 supports 引用",
                "severity": "error",
            }
        ],
    }
    monkeypatch.setattr(chat, "run_once", lambda *args, **kwargs: failure)

    assert cli.main(["--root", str(root), "chat", "--text", "研究教学公司"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["data"]["run_id"] == "run-failed"
    assert payload["issues"][0]["code"] == "missing_authoritative_support"


def test_ambiguous_symbol_requires_selection_before_formal_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    plan = RoutePlan(
        intent="fixed_research",
        company_name="保利",
        ticker=None,
        confidence=0.5,
        reason="简称有歧义",
    )
    candidate = SymbolCandidate(
        candidate_id="1",
        ticker="HK.06049",
        name="保利物业",
        market="HK",
        security_type="STOCK",
        source="futu",
        verified=True,
    )
    monkeypatch.setattr(chat.router, "route", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        chat.symbols,
        "resolve",
        lambda *args, **kwargs: symbols.ResolutionResult([candidate]),
    )
    collect_called: list[str] = []
    monkeypatch.setattr(
        chat.research, "collect", lambda *args, **kwargs: collect_called.append("called")
    )

    first = chat.run_once(root, "帮我研究一下保利")
    assert first["status"] == "awaiting_symbol_selection"
    assert first["candidates"][0]["ticker"] == "HK.06049"
    session_id = str(first["session_id"])

    selected = chat.run_once(root, "1", session_id=session_id)
    assert selected["status"] == "awaiting_confirmation"
    assert selected["plan"]["ticker"] == "HK.06049"
    assert "保利物业" in str(selected["message"])
    assert collect_called == []


def test_router_repairs_one_invalid_protocol_response(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    valid = {
        "intent": "fixed_research",
        "company_name": "中国电信",
        "ticker": "HK.00728",
        "question": None,
        "focus": "general",
        "confidence": 0.9,
        "reason": "用户要求正式报告",
        "requires_confirmation": True,
    }
    responses = iter(
        [
            {"choices": [{"message": {"content": '{"intent":"research"}'}}]},
            {"choices": [{"message": {"content": json.dumps(valid, ensure_ascii=False)}}]},
        ]
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(router.model_request, "request_deepseek", lambda **kwargs: next(responses))

    plan = router.route(root, "帮我研究一下中国电信")

    assert plan.intent == "fixed_research"
    assert plan.ticker == "HK.00728"


def test_router_treats_fixed_research_phrase_as_formal_intent(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "intent": "clarify",
                            "company_name": "保利",
                            "ticker": None,
                            "question": None,
                            "focus": None,
                            "confidence": 0.4,
                            "reason": "简称有歧义",
                            "requires_confirmation": False,
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(router.model_request, "request_deepseek", lambda **kwargs: payload)

    plan = router.route(root, "帮我研究一下保利")

    assert plan.intent == "fixed_research"
    assert plan.ticker is None
    assert plan.requires_confirmation is True


def test_progress_sink_writes_short_events_to_stderr(capsys) -> None:
    sink = progress.StderrProgress()
    sink(progress.ProgressEvent(phase="collect", status="started", message="开始采集"))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[collect] 开始采集" in captured.err


def test_chat_text_only_continues_for_interactive_results() -> None:
    assert cli._chat_needs_followup({"status": "awaiting_confirmation"}) is True
    assert cli._chat_needs_followup({"status": "awaiting_finish"}) is True
    assert cli._chat_needs_followup({"status": "waiting_user"}) is True
    assert (
        cli._chat_needs_followup(
            {"status": "completed", "result": {"report": {"answer": "临时分析"}}}
        )
        is True
    )
    assert cli._chat_needs_followup({"status": "completed", "result": {"run_id": "run-1"}}) is False


def test_chat_text_reuses_session_in_a_terminal(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    run_dir = root / "data" / "stocks" / "SYNTHETIC" / "runs" / "sample"
    run_dir.mkdir(parents=True)
    responses = iter(
        [
            {"session_id": "chat-1", "status": "awaiting_confirmation", "message": "是否开始？"},
            {"session_id": "chat-1", "status": "awaiting_finish", "message": "是否定稿？"},
            {
                "session_id": "chat-1",
                "status": "completed",
                "result": {
                    "run_id": "run-1",
                    "status": "completed",
                    "run_dir": str(run_dir.relative_to(root)),
                    "score_total": 23,
                    "quality_status": "complete",
                },
            },
        ]
    )
    calls: list[tuple[str, str | None]] = []

    def fake_run_once(root, text, *, session_id=None, model=None, on_progress=None):
        calls.append((text, session_id))
        return next(responses)

    class TerminalStdin:
        def isatty(self) -> bool:
            return True

    class TerminalOutput:
        def __init__(self) -> None:
            self.value = ""

        def isatty(self) -> bool:
            return True

        def write(self, value: str) -> int:
            self.value += value
            return len(value)

        def flush(self) -> None:
            return None

    monkeypatch.setattr(chat, "run_once", fake_run_once)
    monkeypatch.setattr(cli.sys, "stdin", TerminalStdin())
    terminal = TerminalOutput()
    monkeypatch.setattr(cli.sys, "stdout", terminal)
    answers = iter(["确认开始", "确认定稿"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert cli.main(["--root", str(root), "chat", "--text", "研究教学公司"]) == 0
    assert calls == [("研究教学公司", None), ("确认开始", "chat-1"), ("确认定稿", "chat-1")]
    assert "是否开始？" in terminal.value
    assert "正式报告已完成并冻结" in terminal.value
    summary = terminal.value.split("正式报告已完成并冻结", maxsplit=1)[1]
    assert "chat-1" not in summary
    assert "session_id" not in summary


def test_chat_finish_uses_human_summary_in_a_terminal(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    run_dir = root / "data" / "stocks" / "SYNTHETIC" / "runs" / "sample"
    run_dir.mkdir(parents=True)
    result = {
        "session_id": "chat-session-1",
        "status": "completed",
        "result": {
            "run_id": "run-1",
            "status": "completed",
            "run_dir": str(run_dir.relative_to(root)),
            "score_total": 23,
            "quality_status": "complete",
        },
    }

    class TerminalOutput:
        def __init__(self) -> None:
            self.value = ""

        def isatty(self) -> bool:
            return True

        def write(self, value: str) -> int:
            self.value += value
            return len(value)

        def flush(self) -> None:
            return None

    terminal = TerminalOutput()
    monkeypatch.setattr(cli.sys, "stdout", terminal)

    cli._print_chat_response(root, result)

    assert "正式报告已完成并冻结" in terminal.value
    assert "质量：完整" in terminal.value
    assert "护城河严格证据总分：23 / 60" in terminal.value
    assert "护城河可用暂定总分：23 / 60" in terminal.value
    assert str((run_dir / "company.md").resolve()) in terminal.value
    assert "chat-session-1" not in terminal.value
    assert '"run_id"' not in terminal.value


def test_chat_text_finish_uses_summary_in_tty_without_session_json(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    run_dir = root / "data" / "stocks" / "SYNTHETIC" / "runs" / "sample"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(
        chat,
        "run_once",
        lambda *args, **kwargs: {
            "session_id": "chat-session-2",
            "status": "completed",
            "result": {
                "run_id": "run-2",
                "status": "completed",
                "run_dir": str(run_dir.relative_to(root)),
                "score_total": 23,
                "quality_status": "complete",
            },
        },
    )

    class TerminalOutput:
        def __init__(self) -> None:
            self.value = ""

        def isatty(self) -> bool:
            return True

        def write(self, value: str) -> int:
            self.value += value
            return len(value)

        def flush(self) -> None:
            return None

    terminal = TerminalOutput()
    monkeypatch.setattr(cli.sys, "stdout", terminal)

    assert (
        cli.main(
            ["--root", str(root), "chat", "--text", "确认定稿", "--session-id", "chat-session-2"]
        )
        == 0
    )

    assert "正式报告已完成并冻结" in terminal.value
    assert "chat-session-2" not in terminal.value
    assert '"ok"' not in terminal.value


def test_writer_failure_can_resume_same_chat_run_without_recollect(tmp_path, monkeypatch):
    from wonder_stockresearch.fixed import writer

    root = make_root(tmp_path)
    plan = RoutePlan(intent="fixed_research", ticker="SYNTHETIC", confidence=1, reason="test")
    monkeypatch.setattr(router, "route", lambda *a, **kw: plan)
    collected = []
    monkeypatch.setattr(
        research,
        "collect",
        lambda root, run_id, *a, **kw: (collected.append(run_id) or {"succeeded": 1}, []),
    )

    def fail(*a, **kw):
        raise writer.WriterError("invalid_section_shape", "shape mismatch")

    monkeypatch.setattr(writer, "write_draft", fail)
    first = chat.run_once(root, "研究教学公司")
    failed = chat.run_once(root, "确认", session_id=first["session_id"])
    assert failed["status"] == "failed"
    assert failed["failed_step"] == "write-draft"
    assert failed["issues"][0]["code"] == "invalid_section_shape"
    monkeypatch.setattr(writer, "write_draft", lambda *a, **kw: ({}, []))
    monkeypatch.setattr(research, "validate_run", lambda *a, **kw: [])
    result = chat.run_once(root, "重试", session_id=first["session_id"])
    assert result["status"] == "awaiting_finish"
    assert result["run_id"] == failed["run_id"]
    assert collected == [failed["run_id"]]
    with storage.open_db(root) as c:
        assert len(storage.list_runs(c, "SYNTHETIC")) == 1


def test_finish_failure_preserves_session_and_diagnostics(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    session_id = chat._new_session(root, "test")
    run_id = research.start_run(root, "SYNTHETIC")["run_id"]
    chat._update(
        root,
        session_id,
        history_json="[]",
        status="awaiting_finish",
        pending={"action": "finish", "run_id": run_id},
        run_id=run_id,
    )

    def fail(*a, **kw):
        raise research.ResearchError("semantic_review_stale", "草稿已变化", exit_code=2)

    monkeypatch.setattr(research, "finish_run", fail)
    result = chat.run_once(root, "确认定稿", session_id=session_id)
    assert result["status"] == "failed"
    assert result["failed_step"] == "finish"
    assert result["run_id"] == run_id
    assert result["issues"][0]["code"] == "semantic_review_stale"
    assert chat._pending(chat._row(root, session_id))["action"] == "finish"


def test_failed_chat_json_returns_nonzero_with_run_id(tmp_path, monkeypatch, capsys):
    root = make_root(tmp_path)
    monkeypatch.setattr(
        chat,
        "run_once",
        lambda *a, **kw: {
            "status": "failed",
            "run_id": "test-run",
            "session_id": "test-session",
            "issues": [
                {
                    "code": "invalid_json",
                    "location": "write-draft",
                    "message": "invalid",
                    "severity": "error",
                }
            ],
        },
    )
    assert cli.main(["--root", str(root), "chat", "--text", "研究公司"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["data"]["run_id"] == "test-run"
