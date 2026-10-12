"""受控 Agent 工具目录与 Skill 工具授权。

目录只登记 Python 实现的函数和它们的输入 schema。Skill manifest 只能引用已有 ID；
模型能看见的工具集合还要经过 capability 与所选 Skill 的二次解析。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from wonder_stockresearch.interactive import agent_tools as handlers
from wonder_stockresearch.interactive.query_workspace import FUNCTION_GUIDANCE
from wonder_stockresearch.interactive.tool_contracts import (
    AnalysisArtifactArgs,
    AnalysisCodeArgs,
    AnalysisDatasetArgs,
    AnalysisFunctionArgs,
    AnalysisResultArgs,
    CompanyQueryArgs,
    EmptyArgs,
    FetchFinancialArgs,
    FinancialQueryArgs,
    MemoReadArgs,
    ReadonlySqlArgs,
    SearchArgs,
    SourceReadArgs,
    ToolContext,
    TrustedCalculationArgs,
)
from wonder_stockresearch.models import StrictModel

REGISTRY_VERSION = "6"
ToolHandler = Callable[[ToolContext, Any], dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    description: str
    arguments: type[StrictModel]
    capabilities: frozenset[str]
    handler: ToolHandler
    default_enabled: bool = True


class ToolRegistryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


_REGISTRY: dict[str, ToolDefinition] = {
    "get_run_status": ToolDefinition(
        "get_run_status",
        "读取研究 run 状态、来源数量和草稿/步骤计数摘要；公司基本资料请直接 query_company_data。",
        EmptyArgs,
        frozenset({"interactive_research"}),
        handlers.get_run_status,
    ),
    "list_sources": ToolDefinition(
        "list_sources",
        "列出当前公司或 run 已归档来源的元数据和 source_id。",
        EmptyArgs,
        frozenset({"interactive_research"}),
        handlers.list_sources,
    ),
    "read_source": ToolDefinition(
        "read_source",
        "分段读取已归档来源；引用时只能使用真实返回的 locator。",
        SourceReadArgs,
        frozenset({"interactive_research"}),
        handlers.read_source,
    ),
    "search_archived_sources": ToolDefinition(
        "search_archived_sources",
        "在当前 run 的已归档文本中做受限关键词搜索。",
        SearchArgs,
        frozenset({"interactive_research"}),
        handlers.search_archived_sources,
    ),
    "read_report": ToolDefinition(
        "read_report",
        "读取当前 run 已完成的正式 company.md，不能修改它。",
        EmptyArgs,
        frozenset({"interactive_research"}),
        handlers.read_report,
    ),
    "list_memos": ToolDefinition(
        "list_memos",
        "列出该公司的长期 Memo。",
        EmptyArgs,
        frozenset({"interactive_research"}),
        handlers.list_memos,
    ),
    "read_memo": ToolDefinition(
        "read_memo",
        "读取一条已经由用户明确保存的公司 Memo。",
        MemoReadArgs,
        frozenset({"interactive_research"}),
        handlers.read_memo,
    ),
    "query_archived_financials": ToolDefinition(
        "query_archived_financials",
        "检索归档财务数值与来源定位；query 可用空格分隔多个关键词，按任一词命中。",
        FinancialQueryArgs,
        frozenset({"interactive_research"}),
        handlers.query_archived_financials,
        default_enabled=False,
    ),
    "query_company_data": ToolDefinition(
        "query_company_data",
        "查询 company_latest 固定版本；指标位置未知先 fields/query 并省略 dataset 跨数据集找字段。"
        "目录不可引用，periods 仅最近五期，period_count/earliest_period/latest_period 给覆盖。"
        "facts 返回值/期间/单位/定位；field_keys、periods 精确筛选，period_kind=annual 限年度。"
        "按 next_offset 续页；query 按任一词/有限同义词匹配，不证明口径相同。",
        CompanyQueryArgs,
        frozenset({"interactive_research"}),
        handlers.query_company_data,
        default_enabled=False,
    ),
    "fetch_financial_data": ToolDefinition(
        "fetch_financial_data",
        "归档缺数据时按需补取当前公司的富途只读基本/财务数据：profile 为公司概况；statements 含三表和关键指标；"
        "operational_efficiency 为经营效率；revenue_breakdown 为营收构成。query 按任一关键词检索，"
        "不支持任意 API 或其他公司。返回独立归档来源，后续 SQL 自动可见；离线模式拒绝联网。",
        FetchFinancialArgs,
        frozenset({"interactive_research"}),
        handlers.fetch_financial_data,
        default_enabled=False,
    ),
    "run_trusted_calculation": ToolDefinition(
        "run_trusted_calculation",
        "运行 Python 固定计算：net_profit_yoy_growth、roe_series、capex_to_revenue 或 cash_capex_proxy。",
        TrustedCalculationArgs,
        frozenset({"interactive_research"}),
        handlers.run_trusted_calculation,
        default_enabled=False,
    ),
    "create_analysis_dataset": ToolDefinition(
        "create_analysis_dataset",
        "从已归档财务数值导出最小只读 JSON 数据集，供一次受限临时计算使用。",
        AnalysisDatasetArgs,
        frozenset({"interactive_research"}),
        handlers.create_analysis_dataset,
        default_enabled=False,
    ),
    "run_analysis_code": ToolDefinition(
        "run_analysis_code",
        "只在已创建的数据集上执行受限 Python AST 子集；不能访问网络、环境变量、SQLite 或项目文件。",
        AnalysisCodeArgs,
        frozenset({"interactive_research"}),
        handlers.run_analysis_code,
        default_enabled=False,
    ),
    "read_analysis_result": ToolDefinition(
        "read_analysis_result",
        "读取某次已审计临时计算的结果和来源引用。",
        AnalysisResultArgs,
        frozenset({"interactive_research"}),
        handlers.read_analysis_result,
        default_enabled=False,
    ),
    "describe_analysis_schema": ToolDefinition(
        "describe_analysis_schema",
        "说明当前 Turn 的隔离财务事实快照表、字段和引用规则；不返回来源全文。",
        EmptyArgs,
        frozenset({"interactive_research"}),
        handlers.describe_analysis_schema,
        default_enabled=False,
    ),
    "run_readonly_sql": ToolDefinition(
        "run_readonly_sql",
        "隔离 SQLite 单条 SELECT/CTE；先 describe_analysis_schema 确认实际字段；基础 financial_facts 字段为 fact_id,label,value_decimal,period,"
        "currency,unit,source_id,locator,source_sha256。保留 fact_id 获得引用，筛选最小数据再计算。",
        ReadonlySqlArgs,
        frozenset({"interactive_research"}),
        handlers.run_readonly_sql,
        default_enabled=False,
    ),
    "run_analysis_function": ToolDefinition(
        "run_analysis_function",
        "只处理对应 SQL 行，不访问 SQLite。" + FUNCTION_GUIDANCE,
        AnalysisFunctionArgs,
        frozenset({"interactive_research"}),
        handlers.run_analysis_function,
        default_enabled=False,
    ),
    "read_analysis_artifact": ToolDefinition(
        "read_analysis_artifact",
        "读取当前 Turn 某次 SQL/函数分析的审计结果、哈希和可引用来源。",
        AnalysisArtifactArgs,
        frozenset({"interactive_research"}),
        handlers.read_analysis_artifact,
        default_enabled=False,
    ),
}

# 执行索引由定义派生；工具名称、schema 与函数只在上方绑定一次。
_HANDLERS = {name: item.handler for name, item in _REGISTRY.items()}

# 直接 ask 的显式默认目录；注册新工具不会自动授权。v4 增加财务补取和 QueryWorkspace。
LEGACY_TOOL_IDS = (
    "get_run_status",
    "list_sources",
    "read_source",
    "search_archived_sources",
    "read_report",
    "list_memos",
    "read_memo",
    "query_archived_financials",
    "run_trusted_calculation",
    "create_analysis_dataset",
    "run_analysis_code",
    "read_analysis_result",
    "fetch_financial_data",
    "describe_analysis_schema",
    "run_readonly_sql",
    "run_analysis_function",
    "read_analysis_artifact",
)


def get(tool_id: str) -> ToolDefinition | None:
    return _REGISTRY.get(tool_id)


def names() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def has_handler(tool_id: str) -> bool:
    return tool_id in _HANDLERS


def execute(tool_id: str, root: Path, row: dict[str, Any], arguments: object) -> dict[str, Any]:
    handler = _HANDLERS.get(tool_id)
    if handler is None:
        raise ToolRegistryError(
            "tool_handler_unavailable", f"工具没有可执行的 Python handler：{tool_id}"
        )
    definition = _REGISTRY[tool_id]
    try:
        parsed = definition.arguments.model_validate(arguments)
    except ValidationError as error:
        rendered = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', ()))}：{item.get('msg', '')}"
            for item in error.errors()[:3]
        )
        raise ToolRegistryError("invalid_tool_arguments", f"输入不符合要求：{rendered}") from error
    context = ToolContext(root, row)
    handlers.validate_context(context)
    return handler(context, parsed)


def definitions_for(
    capability: str, tool_ids: list[str] | tuple[str, ...] | None = None
) -> tuple[ToolDefinition, ...]:
    """按 capability 与 Skill grants 解析稳定、去重后的工具集合。

    未指定显式 grants 时保留 legacy CLI `ask` 的完整工具集；调用方若传入空列表，
    只取得该 capability 的默认工具。
    """
    requested = LEGACY_TOOL_IDS if tool_ids is None else tuple(tool_ids)
    selected: list[str] = [
        name
        for name, item in _REGISTRY.items()
        if capability in item.capabilities and item.default_enabled
    ]
    for name in requested:
        definition = _REGISTRY.get(name)
        if definition is None:
            raise ToolRegistryError("unknown_skill_tool", f"Skill 引用了未注册的工具：{name}")
        if not has_handler(name):
            raise ToolRegistryError(
                "tool_handler_unavailable", f"工具没有可执行的 Python handler：{name}"
            )
        if capability not in definition.capabilities:
            raise ToolRegistryError(
                "tool_capability_mismatch", f"工具 {name} 不适用于 capability：{capability}"
            )
        if name not in selected:
            selected.append(name)
    for name in selected:
        if not has_handler(name):
            raise ToolRegistryError(
                "tool_handler_unavailable", f"工具没有可执行的 Python handler：{name}"
            )
    return tuple(_REGISTRY[name] for name in selected)


def validate_skill_tools(
    tool_ids: list[str] | tuple[str, ...], capabilities: tuple[str, ...]
) -> None:
    """检查 manifest 中每个工具至少适用于该 Skill 声明的一项 capability。"""
    if len(set(tool_ids)) != len(tool_ids):
        raise ToolRegistryError("duplicate_skill_tool", "Skill manifest 的 tools 不能重复")
    for name in tool_ids:
        definition = _REGISTRY.get(name)
        if definition is None:
            raise ToolRegistryError("unknown_skill_tool", f"Skill 引用了未注册的工具：{name}")
        if not has_handler(name):
            raise ToolRegistryError(
                "tool_handler_unavailable", f"工具没有可执行的 Python handler：{name}"
            )
        if not set(capabilities).intersection(definition.capabilities):
            raise ToolRegistryError(
                "tool_capability_mismatch",
                f"工具 {name} 与 Skill 声明的 capability 不兼容",
            )


def granted_tool_ids(tool_ids: list[str] | tuple[str, ...], capability: str) -> tuple[str, ...]:
    """返回本次 Agent 可见工具；即使调用方伪造名称也会在执行前被拒绝。"""
    return tuple(item.tool_id for item in definitions_for(capability, list(tool_ids)))


def schema_catalog(
    tool_ids: list[str] | tuple[str, ...] | None, capability: str
) -> list[dict[str, Any]]:
    return [
        {
            "name": item.tool_id,
            "description": item.description,
            "arguments": _prompt_schema(item.arguments.model_json_schema()),
        }
        for item in definitions_for(capability, list(tool_ids) if tool_ids is not None else None)
    ]


def _prompt_schema(value: Any) -> Any:
    """省略不影响校验的展示标题，保留类型、必填、默认值和所有约束。"""
    if isinstance(value, dict):
        return {key: _prompt_schema(child) for key, child in value.items() if key != "title"}
    if isinstance(value, list):
        return [_prompt_schema(child) for child in value]
    return value
