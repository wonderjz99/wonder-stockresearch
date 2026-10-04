from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import agent, research, storage
from wonder_stockresearch.models import (
    EvidenceRef,
    InteractiveQuestion,
    MemoRequest,
    ResearchPacket,
    SourceRequest,
)


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


def draft_run(root: Path) -> str:
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet_for())
    return run_id


def final_payload(source_id: str) -> dict[str, object]:
    return {
        "type": "final",
        "answer": "这是一次临时分析，不会修改正式报告。",
        "claims": [
            {
                "text": "教学资料支持该临时判断。",
                "evidence": [
                    {"source_id": source_id, "locator": "第 1 页，教学段落", "relation": "supports"}
                ],
            }
        ],
        "evidence_refs": [],
        "counterarguments": ["资料范围有限，仍需下一次复核。"],
        "gaps": ["缺少新的行业对比资料。"],
        "suggested_next_checks": ["下一次复评时补充同行数据。"],
    }


def test_interactive_agent_creates_temporary_report_without_touching_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    run_id = draft_run(root)
    source_id = copy_synthetic_packet()["sources"][0]["id"]
    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        lambda root, messages, model: (final_payload(source_id), 42),
    )

    result = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="这家公司当前最大的风险是什么？"),
    )

    assert result["status"] == "completed"
    assert result["report"]["user_question"] == "这家公司当前最大的风险是什么？"
    status = research.get_status(root, run_id)
    assert status["status"] == "draft"
    assert status["answer_count"] == 24
    with storage.open_db(root) as connection:
        sessions = connection.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0]
        reports = connection.execute("SELECT COUNT(*) FROM interactive_reports").fetchone()[0]
        events = connection.execute("SELECT COUNT(*) FROM agent_events").fetchone()[0]
    assert sessions == 1
    assert reports == 1
    assert events >= 2


def test_interactive_agent_reads_archived_source_before_final_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    run_id = draft_run(root)
    document = tmp_path / "risk.txt"
    document.write_text("云业务竞争加剧，需要持续跟踪。\n", encoding="utf-8")
    added = research.source_add(
        root,
        run_id,
        SourceRequest(kind="primary", title="风险材料", file=str(document)),
    )
    source_id = str(added["source_id"])
    decisions = iter(
        [
            (
                {
                    "type": "tool_call",
                    "tool": "read_source",
                    "arguments": {"source_id": source_id, "start_line": 1, "limit_lines": 20},
                },
                10,
            ),
            (final_payload(source_id), 12),
        ]
    )
    monkeypatch.setattr(agent, "_api_call_for_root", lambda root, messages, model: next(decisions))

    result = agent.ask(root, "SYNTHETIC", InteractiveQuestion(question="风险是什么？"))

    assert result["status"] == "completed"
    with storage.open_db(root) as connection:
        event_types = [row["event_type"] for row in storage.list_agent_events(connection, result["session_id"])]
    assert event_types == ["question", "decision", "tool_result", "decision"]


def test_memo_save_is_explicit_and_versioned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path)
    draft_run(root)
    source_id = copy_synthetic_packet()["sources"][0]["id"]
    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        lambda root, messages, model: (final_payload(source_id), 10),
    )
    result = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="需要长期跟踪什么？"),
    )
    session_id = str(result["session_id"])
    reference = EvidenceRef(
        source_id=source_id,
        locator="第 1 页，教学段落",
        relation="supports",
    )

    saved = agent.memo_save(
        root,
        session_id,
        MemoRequest(
            title="长期跟踪事项",
            content="下一次复评时补充同行数据。",
            source_refs=[reference],
            tags=["follow-up"],
        ),
    )
    assert saved["saved"] is True
    memo = saved["memo"]
    assert memo["version"] == 1
    assert agent.memo_list(root, "SYNTHETIC")["memos"][0]["title"] == "长期跟踪事项"
    assert agent.memo_read(root, memo["memo_id"])["content"] == "下一次复评时补充同行数据。"

    saved_again = agent.memo_save(
        root,
        session_id,
        MemoRequest(
            title="长期跟踪事项",
            content="补充同行数据后再复核。",
            source_refs=[reference],
        ),
    )
    assert saved_again["memo"]["version"] == 2


def test_agent_rejects_unknown_source_in_temporary_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    draft_run(root)
    unknown = "99999999-9999-4999-8999-999999999999"
    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        lambda root, messages, model: (final_payload(unknown), 10),
    )

    with pytest.raises(agent.AgentError) as exc_info:
        agent.ask(root, "SYNTHETIC", InteractiveQuestion(question="测试虚假引用"))
    assert exc_info.value.code == "unknown_source_reference"
    sessions = agent.get_status(root, next(iter(_session_ids(root))))
    assert sessions["status"] == "failed"


def _session_ids(root: Path) -> list[str]:
    with storage.open_db(root) as connection:
        rows = connection.execute("SELECT id FROM agent_sessions").fetchall()
    return [str(row[0]) for row in rows]


def test_cli_interactive_commands_use_json_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    from wonder_stockresearch.cli import main

    root = make_root(tmp_path)
    draft_run(root)
    source_id = copy_synthetic_packet()["sources"][0]["id"]
    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        lambda root, messages, model: (final_payload(source_id), 10),
    )
    question = tmp_path / "question.json"
    question.write_text(json.dumps({"question": "测试交互入口"}), encoding="utf-8")
    assert main(["--root", str(root), "ask", "SYNTHETIC", "--input", str(question)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    session_id = payload["data"]["session_id"]

    assert main(["--root", str(root), "ask-status", session_id]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["data"]["status"] == "completed"
