"""Skill 可声明的受控 Python capability。

Skill 的 Markdown 只能选择这里已经注册的能力。它不能通过文本获得文件、网络、
数据库或代码执行权限；实际副作用仍由对应的 Python 服务实施并记录。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field

from .models import StrictModel


class FixedResearchInput(StrictModel):
    ticker: str = Field(min_length=1)
    skill_names: list[str] = Field(default_factory=list)


class FixedResearchOutput(StrictModel):
    run_id: str = Field(min_length=1)


class InteractiveResearchInput(StrictModel):
    ticker: str = Field(min_length=1)
    question: str = Field(min_length=1)
    skill_names: list[str] = Field(default_factory=list)


class InteractiveResearchOutput(StrictModel):
    session_id: str = Field(min_length=1)


class AnalysisWorkspaceInput(StrictModel):
    ticker: str = Field(min_length=1)
    question: str = Field(min_length=1)


class AnalysisWorkspaceOutput(StrictModel):
    workspace_id: str = Field(min_length=1)


@dataclass(frozen=True)
class CapabilityDefinition:
    name: str
    description: str
    input_model: type[StrictModel]
    output_model: type[StrictModel]
    permissions: tuple[str, ...]
    audit_target: str
    available: bool


_REGISTRY: dict[str, CapabilityDefinition] = {
    "fixed_research": CapabilityDefinition(
        name="fixed_research",
        description="创建标准 ResearchRun，并由受控 Python 流程采集、写作、校验和等待 finish 确认。",
        input_model=FixedResearchInput,
        output_model=FixedResearchOutput,
        permissions=("create_research_run", "read_only_collect", "write_draft"),
        audit_target="run_steps.skill_selection",
        available=True,
    ),
    "interactive_research": CapabilityDefinition(
        name="interactive_research",
        description="基于已归档资料运行只读交互研究并生成临时报告。",
        input_model=InteractiveResearchInput,
        output_model=InteractiveResearchOutput,
        permissions=("read_archived_sources", "create_agent_session", "create_interactive_report"),
        audit_target="chat_sessions.history_json.selection_audit",
        available=True,
    ),
    "analysis_workspace": CapabilityDefinition(
        name="analysis_workspace",
        description="在已归档资料导出的最小只读数据集上执行受限临时计算，并保存来源与执行审计。",
        input_model=AnalysisWorkspaceInput,
        output_model=AnalysisWorkspaceOutput,
        permissions=("read_exported_dataset", "write_temporary_files", "run_limited_code"),
        audit_target="analysis_runs",
        available=True,
    ),
}


def get(name: str) -> CapabilityDefinition | None:
    return _REGISTRY.get(name)


def names() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
