"""虚构数据、脚本模型：唯一公司备忘录与确定性 save，不访问网络。"""

from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from tests.fixtures import copy_synthetic_packet
from tests.test_agent import draft_run, final_payload
from tests.test_agent_recovery_working_record import last_result, metric_archive
from tests.test_storage import make_root
from wonder_stockresearch import agent, chat, cli, storage
from wonder_stockresearch.interactive import agent_model, agent_tools, company_memo
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.interactive.tool_contracts import EmptyArgs, MemoReadArgs, ToolContext
from wonder_stockresearch.models import InteractiveFollowup, InteractiveQuestion


def synthetic_answer(root: Path, *, question: str = "ROE 分析") -> dict:
    draft_run(root)
    payload = final_payload(copy_synthetic_packet()["sources"][0]["id"])
    return agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question=question),
        model_client=agent_model.ScriptedAgentModelClient([(payload, 10)]),
    )


def link_chat(root: Path, result: dict) -> str:
    chat_id = chat._new_session(root, "临时分析")
    row = chat._row(root, chat_id)
    chat._update(
        root,
        chat_id,
        history_json=row["history_json"],
        status="completed",
        ticker=result["ticker"],
        last_agent_session_id=result["session_id"],
        clear_pending=True,
    )
    return chat_id


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_chat_save_is_offline_idempotent_and_followup_appends_to_same_company(
    tmp_path: Path,
    monkeypatch,
    runtime: str,
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    first = synthetic_answer(root)
    chat_id = link_chat(root, first)

    def forbidden(*args, **kwargs):
        raise AssertionError("save 不得调用模型/Router/追问")

    monkeypatch.setattr(chat.router, "route", forbidden)
    with monkeypatch.context() as patch:
        patch.setattr(agent, "followup", forbidden)
        saved = chat.run_once(root, "save", session_id=chat_id)
        again = chat.run_once(root, "SAVE", session_id=chat_id)
    assert saved["status"] == "memo_saved" and cli._chat_needs_followup(saved)
    assert again["result"]["already_saved"] and again["result"]["memo"]["version"] == 1
    notebook_id = saved["result"]["memo"]["memo_id"]
    path = root / saved["result"]["path"]
    assert path.is_file()
    note = agent.memo_read(root, notebook_id)
    assert note["entry_count"] == 1 and note["entries"][0]["source_refs"]
    assert "缺少新的行业对比资料" in note["content"]
    assert "教学资料支持该临时判断" in note["content"]
    assert first["turn_id"] in note["content"]
    second = agent.followup(
        root,
        first["session_id"],
        InteractiveFollowup(question="再分析盈利质量"),
        model_client=agent_model.ScriptedAgentModelClient(
            [
                (
                    {
                        "type": "final",
                        "answer": "资料不足，盈利质量未完成。",
                        "gaps": ["没有相关证据"],
                    },
                    10,
                ),
            ]
        ),
    )
    saved_second = chat.run_once(root, "/save", session_id=chat_id)
    assert saved_second["result"]["memo"]["memo_id"] == notebook_id
    assert saved_second["result"]["memo"]["version"] == 2
    assert len(agent.memo_list(root, "SYNTHETIC")["memos"]) == 1
    assert agent.memo_read(root, "SYNTHETIC")["entry_count"] == 2
    assert "没有相关证据" in path.read_text()
    assert second["turn_id"] in path.read_text()
    assert path.read_text() == agent.memo_read(root, notebook_id)["content"]
    previous = agent.memo_read(root, notebook_id, version=1)
    assert previous["entry_count"] == 1 and second["turn_id"] not in previous["content"]
    with storage.open_db(root) as connection:
        assert connection.execute("SELECT COUNT(*) FROM company_notebooks").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM company_memos").fetchone()[0] == 2
        assert len(storage.list_interactive_reports(connection, first["session_id"])) == 2
        assert storage.get_agent_turn(connection, second["turn_id"])["used_tokens"] == 10
        history = json.loads(storage.get_chat_session(connection, chat_id)["history_json"])
        assert any(item["content"] == "save" and item["role"] == "user" for item in history)


def test_no_session_or_pending_action_is_not_saved_or_routed(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    monkeypatch.setattr(chat.router, "route", lambda *a, **k: pytest.fail("不应路由 save"))
    assert chat.run_once(root, "save")["status"] == "failed"
    chat_id = chat._new_session(root, "正式研究等待确认")
    row = chat._row(root, chat_id)
    chat._update(root, chat_id, history_json=row["history_json"], pending={"action": "finish"})
    result = chat.run_once(root, "save", session_id=chat_id)
    assert result["status"] == "failed" and cli._chat_needs_followup(result)
    assert agent.memo_list(root, "HK.00700")["memos"] == []


def test_failed_latest_turn_never_saves_previous_report_and_foreign_turn_is_rejected(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    first = synthetic_answer(root)
    with pytest.raises(AgentError):
        agent.followup(
            root,
            first["session_id"],
            InteractiveFollowup(question="失败问题"),
            model_client=agent_model.ScriptedAgentModelClient(
                [
                    ({"type": "tool_call", "tool": "unauthorized", "arguments": {}}, 10),
                ]
            ),
        )
    with pytest.raises(AgentError, match="不能保存旧回答"):
        agent.memo_save_report(root, first["session_id"])
    other = synthetic_answer(root, question="其他会话")
    with pytest.raises(AgentError, match="目标问题"):
        agent.memo_save_report(root, other["session_id"], turn_id=first["turn_id"])
    assert agent.memo_list(root, "SYNTHETIC")["memos"] == []
    assert agent.memo_save_report(root, first["session_id"], turn_id=first["turn_id"])["saved"]


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_save_collects_claim_only_refs_and_rejects_changed_archives(
    tmp_path: Path,
    monkeypatch,
    runtime: str,
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    metric_archive(root)
    count = 0

    def decide(_root, messages, _model):
        nonlocal count
        count += 1
        if count == 1:
            return {
                "type": "tool_call",
                "tool": "run_readonly_sql",
                "arguments": {
                    "sql": "SELECT fact_id,period,value_decimal FROM financial_facts LIMIT 1",
                },
            }, 10
        refs = [
            {key: ref[key] for key in ("source_id", "locator", "relation")}
            for ref in last_result(messages)["citable_refs"]
        ]
        return {
            "type": "final",
            "answer": "已有一期数据，四年变化未完成。",
            "claims": [{"text": "一期数据", "evidence": refs}],
            "gaps": ["四年分析未完成"],
        }, 10

    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="查看 ROE", context="company_latest", allow_network=False),
        model_client=agent_model.FunctionAgentModelClient(decide),
        tool_names=["run_readonly_sql"],
    )
    saved = agent.memo_save_report(root, result["session_id"])
    refs = agent.memo_read(root, "US.MU")["entries"][0]["source_refs"]
    assert refs and saved["memo"]["entry_count"] == 1
    with storage.open_db(root) as connection:
        source = storage.get_source(connection, refs[0]["source_id"])
    (root / source["raw_path"]).write_text("changed")
    with pytest.raises(AgentError):
        agent.memo_save_report(root, result["session_id"])
    assert agent.memo_read(root, "US.MU")["entry_count"] == 1


def test_migration_groups_old_records_and_preserves_old_ids_and_versions(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    report = synthetic_answer(root)
    agent.memo_save_report(root, report["session_id"])
    with storage.open_db(root) as connection:
        before = [dict(row) for row in connection.execute("SELECT * FROM company_memos")]
        connection.execute("DROP TABLE company_memo_links")
        connection.execute("DROP TABLE company_notebooks")
        connection.execute("PRAGMA user_version=13")
    storage.initialize(root)
    storage.initialize(root)
    with storage.open_db(root) as connection:
        assert before == [dict(row) for row in connection.execute("SELECT * FROM company_memos")]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 14
    book = agent.memo_list(root, "SYNTHETIC")["memos"]
    assert len(book) == 1 and book[0]["entry_count"] == 1
    assert agent.memo_read(root, before[0]["id"])["content"] == before[0]["content"]


def test_projection_failure_rolls_back_entry_notebook_and_memory(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    result = synthetic_answer(root)
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise sqlite3.OperationalError("injected")

        patch.setattr(storage, "upsert_memory_entry", fail)
        with pytest.raises(sqlite3.OperationalError):
            agent.memo_save_report(root, result["session_id"])
    assert agent.memo_list(root, "SYNTHETIC")["memos"] == []
    with storage.open_db(root) as connection:
        assert connection.execute("SELECT COUNT(*) FROM company_memos").fetchone()[0] == 0
    assert agent.memo_save_report(root, result["session_id"])["memo"]["version"] == 1


def test_export_failure_is_repairable_without_duplicate_entry(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    result = synthetic_answer(root)
    chat_id = link_chat(root, result)
    with monkeypatch.context() as patch:
        patch.setattr(
            company_memo.storage,
            "write_atomic",
            lambda *a, **k: (_ for _ in ()).throw(OSError("injected")),
        )
        failed = chat.run_once(root, "save", session_id=chat_id)
        assert failed["status"] == "failed" and "已保存到数据库" in failed["message"]
    repaired = chat.run_once(root, "save", session_id=chat_id)
    assert repaired["result"]["already_saved"] and repaired["result"]["memo"]["entry_count"] == 1
    assert (root / repaired["result"]["path"]).is_file()


def test_cli_save_without_json_and_read_by_ticker(tmp_path: Path, capsys) -> None:
    root = make_root(tmp_path)
    result = synthetic_answer(root)
    assert cli.main(["--root", str(root), "memo-save", result["session_id"]]) == 0
    saved = json.loads(capsys.readouterr().out)["data"]
    assert saved["saved"] and saved["memo"]["entry_count"] == 1
    assert cli.main(["--root", str(root), "memo-read", "SYNTHETIC"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["memo_id"] == saved["memo"]["memo_id"]


def test_company_notebooks_and_agent_reads_are_isolated(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    notes = {}
    for ticker in ("HK.00700", "US.MU"):
        result = agent.ask(
            root,
            ticker,
            InteractiveQuestion(
                question=f"关于 {ticker} 的观察",
                context="company_latest",
                allow_network=False,
            ),
            model_client=agent_model.ScriptedAgentModelClient(
                [
                    (
                        {
                            "type": "final",
                            "answer": f"{ticker} 资料不足。",
                            "gaps": ["尚未取得数据"],
                        },
                        10,
                    ),
                ]
            ),
        )
        notes[ticker] = agent.memo_save_report(root, result["session_id"])
    tencent = notes["HK.00700"]["memo"]["memo_id"]
    micron = notes["US.MU"]["memo"]["memo_id"]
    assert tencent != micron
    context = ToolContext(root, {"ticker": "HK.00700"})
    listed = agent_tools.list_memos(context, EmptyArgs())
    assert len(listed["memos"]) == 1 and listed["memos"][0]["memo_id"] == tencent
    assert agent_tools.read_memo(context, MemoReadArgs(memo_id=tencent))["ticker"] == "HK.00700"
    with pytest.raises(AgentError, match="不属于当前公司"):
        agent_tools.read_memo(context, MemoReadArgs(memo_id=micron))
    with pytest.raises(AgentError, match="版本不存在"):
        agent.memo_read(root, "HK.00700", version=2)


def test_terminal_save_feedback_and_resume_with_chat_id(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = make_root(tmp_path)
    result = synthetic_answer(root)
    chat_id = link_chat(root, result)
    lines = iter(["save", "退出"])
    monkeypatch.setattr("builtins.input", lambda *args: next(lines))
    monkeypatch.setattr(chat.router, "route", lambda *a, **k: pytest.fail("save 不走路由"))
    assert (
        cli._run_chat_loop(
            root,
            Namespace(model=None, quiet=True),
            session_id=chat_id,
            first_data={"session_id": chat_id, "status": "completed", "result": result},
        )
        == 0
    )
    text = capsys.readouterr().out
    assert "输入 save" in text and "已加入 SYNTHETIC 公司备忘录" in text
    assert cli.main(["--root", str(root), "chat", "--session-id", chat_id, "--text", "save"]) == 0
    resumed = json.loads(capsys.readouterr().out)["data"]
    assert resumed["result"]["already_saved"]
