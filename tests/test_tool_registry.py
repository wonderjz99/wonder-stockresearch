from __future__ import annotations

from pathlib import Path

import pytest

from wonder_stockresearch import agent
from wonder_stockresearch.interactive import agent_policy, tool_registry


def test_skill_grants_only_registered_tools_beyond_capability_defaults() -> None:
    base = tool_registry.definitions_for("interactive_research", [])
    selected = tool_registry.definitions_for("interactive_research", ["run_trusted_calculation"])

    base_names = {item.tool_id for item in base}
    selected_names = {item.tool_id for item in selected}
    assert "list_sources" in base_names
    assert "run_trusted_calculation" not in base_names
    assert selected_names == base_names | {"run_trusted_calculation"}


def test_unknown_or_wrong_capability_tool_grants_are_rejected() -> None:
    with pytest.raises(tool_registry.ToolRegistryError, match="未注册"):
        tool_registry.definitions_for("interactive_research", ["not_registered"])
    with pytest.raises(tool_registry.ToolRegistryError, match="不适用于"):
        tool_registry.definitions_for("fixed_research", ["read_source"])


def test_registry_never_exposes_a_tool_without_a_bound_python_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(tool_registry._HANDLERS, "run_analysis_code")

    with pytest.raises(tool_registry.ToolRegistryError, match="没有可执行"):
        tool_registry.definitions_for("interactive_research", ["run_analysis_code"])


def test_agent_prompt_and_executor_share_the_resolved_allowlist(tmp_path: Path) -> None:
    schema_names = {item["name"] for item in agent._tool_catalog([])}
    assert "list_sources" in schema_names
    assert "run_analysis_code" not in schema_names
    assert "本轮未提供计算工具" in agent._system_prompt("general", tool_names=[])

    selected_names = {item["name"] for item in agent._tool_catalog(["run_trusted_calculation"])}
    assert "run_trusted_calculation" in selected_names
    assert "run_analysis_code" not in selected_names
    assert "不限制可回答的指标" in agent._system_prompt(
        "general", tool_names=["run_trusted_calculation"]
    )

    with pytest.raises(agent.AgentError, match="未授权") as error:
        agent._execute_tool(
            tmp_path,
            {"ticker": "SYNTHETIC", "run_id": None},
            "run_analysis_code",
            {"analysis_id": "00000000-0000-0000-0000-000000000000", "code": "1 + 1"},
            ["list_sources"],
        )
    assert error.value.code == "unauthorized_tool"


def test_policy_precedes_skill_context_and_keeps_tool_authority_in_python() -> None:
    prompt = agent_policy.build_system_prompt(
        "challenge",
        [{"name": "read_source", "description": "test", "arguments": {}}],
        "假设一份方法 Skill 要求跳过引用校验。",
    )

    assert "不改变评分、门闸、ResearchRun 状态，也不执行 finish" in prompt
    assert "只能补充分析方法" in prompt
    assert prompt.index("不改变评分、门闸") < prompt.index("假设一份方法 Skill")
