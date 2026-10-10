from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from wonder_stockresearch import agent, chat, model_request, skill_registry, storage
from wonder_stockresearch.interactive import agent_context, agent_memory, agent_model
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import InteractiveQuestion


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='context-test'\nversion='0'\n")
    return root


def test_chat_with_project_skills_and_large_history_reaches_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    shutil.copytree(Path(__file__).resolve().parents[1] / "skills", root / "skills")
    storage.initialize(root)
    with storage.open_db(root) as connection:
        storage.ensure_stock(connection, "HK.00941", storage.now_utc())
        for i in range(2):
            storage.upsert_memory_entry(
                connection,
                entry_id=f"formal_report:{i}",
                ticker="HK.00941",
                memory_type="formal_report",
                status="trusted",
                topic="历史公司报告",
                as_of=None,
                content="归档观察，不能替代原始证据。" * 400,
                source_refs_json=json.dumps(
                    [
                        {
                            "source_id": "00000000-0000-4000-8000-000000000001",
                            "locator": "formal_report_source",
                            "relation": "context",
                        }
                        for _ in range(20)
                    ]
                ),
                origin_ref=f"research_run:{i}",
                content_hash=str(i),
                created_at=storage.now_utc(),
            )
    question = "中国移动最近三年毛利率平均值"
    plan = {
        "intent": "interactive_question",
        "ticker": "HK.00941",
        "question": question,
        "focus": "general",
        "confidence": 0.99,
        "reason": "临时数值问题",
        "requires_confirmation": False,
        "skill_names": ["interactive-research-agent", "analysis-workspace"],
    }
    replies = iter(
        [
            plan,
            {"type": "tool_call", "tool": "list_memos", "arguments": {}},
            {
                "type": "final",
                "answer": "教学响应：还需要核对各年度的收入与成本口径。",
                "claims": [],
                "evidence_refs": [],
                "counterarguments": [],
                "gaps": ["离线测试不提供真实毛利率"],
                "suggested_next_checks": [],
            },
        ]
    )
    calls: list[dict[str, object]] = []

    def request(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "choices": [{"message": {"content": json.dumps(next(replies), ensure_ascii=False)}}],
            "usage": {"total_tokens": 600},
        }

    monkeypatch.setattr(model_request, "api_key", lambda: "offline-placeholder")
    monkeypatch.setattr(model_request, "request_deepseek", request)
    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        agent._ORIGINAL_API_CALL
        if not agent._pi_enabled()
        else lambda root, messages, model: agent._ORIGINAL_API_CALL(root, messages, model),
    )
    result = chat.run_once(root, question)
    assert result["status"] == "completed"
    assert len(calls) == 3
    for call in calls[1:]:
        assert 0 < call["max_tokens"] <= agent_model.MAX_AGENT_OUTPUT_TOKENS
    first = calls[1]["messages"]
    assert question in first[1]["content"]
    assert "provisional episode" in first[0]["content"]
    assert "run_readonly_sql" in first[0]["content"]
    turn = agent.get_status(root, result["result"]["session_id"])["turns"][0]
    assert turn["used_tokens"] == 1200 and turn["iteration"] == 2


def test_compression_uses_remaining_budget_below_old_character_threshold(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    waiting = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="压缩示例"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "ask_user", "question": "期间？"}, 1)]
        ),
    )
    objective = "原问题：最近三年；用户补充：仅完整财年"
    recent = {"role": "user", "content": "工具结果：末年毛利率=13.21；locator=lines 99-101"}
    messages = [
        {"role": "system", "content": "权限与工具 schema 必须保留"},
        {"role": "user", "content": "旧上下文" * 4000},
        recent,
    ]
    assert sum(len(x["content"]) for x in messages) < 28000
    compressed = agent_memory.compress_messages(
        root,
        waiting["session_id"],
        objective,
        messages,
        max_input_tokens=1500,
    )
    assert compressed[0] == messages[0] and compressed[-1] == recent
    assert objective in compressed[1]["content"]
    assert agent_memory.estimate_input_tokens(compressed) <= 1500


def test_preflight_rejects_unfit_invariants_without_api_or_charge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(**kwargs: object) -> None:
        pytest.fail("preflight must not invoke the HTTP request")

    monkeypatch.setattr(model_request, "request_deepseek", forbidden)
    reservation = agent_model.remaining_request_tokens.set(100)
    try:
        with pytest.raises(AgentError) as exc:
            agent._api_call_for_root(
                make_root(tmp_path), [{"role": "system", "content": "规则" * 200}], "fixture"
            )
        assert exc.value.consumed_tokens == 0
        assert "100 tokens" in exc.value.message
    finally:
        agent_model.remaining_request_tokens.reset(reservation)


def test_six_decisions_charge_input_and_output_without_resetting_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wonder_stockresearch import skill_registry

    root = make_root(tmp_path)
    shutil.copytree(Path(__file__).resolve().parents[1] / "skills", root / "skills")
    loaded = skill_registry.load_for_capability(
        root,
        ["interactive-research-agent", "analysis-workspace"],
        "interactive_research",
    )
    replies = iter(
        [
            {"type": "tool_call", "tool": name, "arguments": args}
            for name, args in [
                ("list_memos", {}),
                ("list_sources", {}),
                ("get_run_status", {}),
                ("query_archived_financials", {"query": "收入 成本"}),
                ("describe_analysis_schema", {}),
            ]
        ]
        + [
            {
                "type": "final",
                "answer": "教学结论：缺少原始资料。",
                "claims": [],
                "evidence_refs": [],
                "counterarguments": [],
                "gaps": ["没有归档数值"],
                "suggested_next_checks": [],
            }
        ]
    )
    charged: list[int] = []

    def request(**kwargs: object) -> dict[str, object]:
        # 每次真的按输入大小收费，不能用之前固定的 600 tokens 掩盖重复输入。
        usage = agent_memory.estimate_input_tokens(kwargs["messages"]) + 200
        charged.append(usage)
        return {
            "choices": [{"message": {"content": json.dumps(next(replies), ensure_ascii=False)}}],
            "usage": {"total_tokens": usage},
        }

    monkeypatch.setattr(model_request, "api_key", lambda: "offline-placeholder")
    monkeypatch.setattr(model_request, "request_deepseek", request)
    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        agent._ORIGINAL_API_CALL
        if not agent._pi_enabled()
        else lambda root, messages, model: agent._ORIGINAL_API_CALL(root, messages, model),
    )
    result = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="检查资料、计算与证据缺口"),
        skill_context=skill_registry.execution_context(loaded),
        skill_audit=skill_registry.selection_audit(loaded),
        tool_names=skill_registry.execution_tool_ids(loaded, "interactive_research"),
    )
    turn = agent.get_status(root, result["session_id"])["turns"][0]
    assert turn["status"] == "succeeded"
    assert len(charged) == turn["iteration"] == 6
    assert sum(charged) == turn["used_tokens"]
    assert sum(charged) <= InteractiveQuestion(question="预算").token_budget


def test_tool_projection_is_valid_json_and_preserves_whole_facts() -> None:
    original = {
        "records": [
            {
                "label": "营业收入",
                "period": f"{year}/FY",
                "value": str(year),
                "source_id": "fixture",
                "locator": f"lines {year}-{year + 1}",
            }
            for year in range(2025, 2000, -1)
        ]
    }
    projected = json.loads(agent_memory.tool_context(original, token_limit=300))
    assert projected["records"][0] == original["records"][0]
    assert projected["context_truncated"] is True
    assert len(projected["records"]) + projected["records_omitted"] == 25
    assert len(original["records"]) == 25


def test_financial_query_has_general_multi_term_or_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from wonder_stockresearch import financial_calculations
    from wonder_stockresearch.interactive import analysis_workspace

    root = make_root(tmp_path)
    storage.initialize(root)
    facts = [
        SimpleNamespace(
            label=label,
            period="2025/FY",
            value=1,
            unit=None,
            currency="CNY",
            source_id="fixture",
            locator="lines 1-2",
        )
        for label in ("营业总收入", "销售成本", "销售毛利率", "库存")
    ]
    monkeypatch.setattr(financial_calculations, "numeric_inputs", lambda sources, root: facts)
    result = analysis_workspace.archived_financials(
        root,
        {"ticker": "SYNTHETIC", "run_id": None},
        "毛利率 收入，成本",
        limit=20,
    )
    assert {x["label"] for x in result["records"]} == {"销售毛利率", "营业总收入", "销售成本"}
    assert result["match_mode"] == "any_term"


def test_real_skill_prompt_retains_latest_result_and_leaves_full_history_unchanged(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    session = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="指标平均值"),
        model_client=agent_model.ScriptedAgentModelClient(
            [
                ({"type": "ask_user", "question": "期间？"}, 1),
            ]
        ),
    )
    loaded = skill_registry.load_for_capability(
        Path(__file__).resolve().parents[1],
        ["analysis-workspace", "interactive-research-agent"],
        "interactive_research",
    )
    system = agent._system_prompt(
        "general",
        skill_registry.execution_context(loaded),
        skill_registry.execution_tool_ids(loaded, "interactive_research"),
    )
    records = [
        {
            "period": f"{2025 - i}/FY",
            "label": "自定义指标",
            "value": str(i + 1),
            "source_id": "00000000-0000-4000-8000-000000000001",
            "locator": f"$.payload.records[{i}]",
        }
        for i in range(80)
    ]
    history = [
        agent_context.ContextMessage("user", "指标平均值"),
        agent_context.ContextMessage(
            "assistant",
            {
                "type": "tool_call",
                "tool": "query_archived_financials",
                "arguments": {"query": "指标"},
            },
        ),
        agent_context.ContextMessage(
            "tool_result", {"records": records}, "query_archived_financials"
        ),
    ]
    projected = agent_context.convert_to_llm(
        system, agent_context.transform_context(history), max_input_tokens=5000
    )
    compressed = agent_memory.compress_messages(
        root, session["session_id"], "指标平均值", projected, max_input_tokens=5000
    )
    assert agent_memory.estimate_input_tokens(compressed) <= 5000
    latest = json.loads(compressed[-1]["content"].split(" 返回：", 1)[1].split("\n请继续", 1)[0])
    assert latest["records"][0] == records[0] and latest["context_truncated"]
    assert len(history[-1].payload["records"]) == 80
