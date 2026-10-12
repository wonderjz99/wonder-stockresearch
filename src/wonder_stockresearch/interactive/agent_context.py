"""运行记录与模型上下文的转换边界，参考 Pi Core 的 transformContext/convertToLlm。

不承担工具权限、状态迁移或预算结算。原始记录留在调用方，返回值只用于一次请求。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from wonder_stockresearch.interactive import agent_memory, agent_results


@dataclass(frozen=True)
class ContextMessage:
    kind: Literal["user", "background", "assistant", "tool_result", "tool_error"]
    payload: Any
    tool: str | None = None


def transform_context(
    history: list[ContextMessage],
    working_memory: dict[str, Any] | None = None,
) -> list[ContextMessage]:
    """保留原问题和最近两组调用/结果；首轮背景不在工具执行后反复注入。"""
    user = [item for item in history if item.kind == "user"]
    exchanges = [item for item in history if item.kind not in {"user", "background"}]
    if exchanges:
        summary = (
            [ContextMessage("background", {"working_memory": working_memory})]
            if working_memory
            else []
        )
        return [*user, *summary, *exchanges[-4:]]
    restored = bool(working_memory) and any(
        key in working_memory
        for key in ("available_input", "field_selection", "last_error", "calculation", "final_only")
    )
    return [
        *history,
        *([ContextMessage("background", {"working_memory": working_memory})] if restored else []),
    ]


def convert_to_llm(
    system: str,
    selected: list[ContextMessage],
    *,
    max_input_tokens: int | None = None,
    remaining_iterations: int | None = None,
) -> list[dict[str, str]]:
    """只转换模型需要的内容；完整 details 不直接拼进提示词。"""
    messages = [{"role": "system", "content": system}]
    # 先给规则与问题留空间，再投影结果；禁止先塞大结果、压缩时把最新结果丢光。
    fixed_cost = agent_memory.estimate_input_tokens(
        [
            {"content": system},
            *[{"content": str(item.payload)} for item in selected if item.kind == "user"],
        ]
    )
    result_limit = (
        1500
        if max_input_tokens is None
        else max(
            128,
            min(1500, max_input_tokens - fixed_cost - 300),
        )
    )
    for item in selected:
        if item.kind == "user":
            messages.append({"role": "user", "content": f"当前问题及用户补充：{item.payload}"})
        elif item.kind == "background":
            messages.append(
                {
                    "role": "user",
                    "content": "辅助上下文（观察不是已证实事实）："
                    + json.dumps(
                        item.payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                }
            )
        elif item.kind == "assistant":
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        item.payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                }
            )
        else:
            label = "返回可恢复错误" if item.kind == "tool_error" else "返回"
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"工具 {item.tool} {label}：{agent_results.model_projection(item.payload, token_limit=result_limit)}\n请继续处理原问题。"
                        "省略不等于没有数据；需要时缩小查询或分段读取。纠正错误后继续，证据不足要说明缺口。"
                    ),
                }
            )
    if remaining_iterations is not None:
        messages[-1]["content"] += f"\n运行状态：含本次还剩 {remaining_iterations} 次决策。"
        if remaining_iterations == 1:
            messages[-1]["content"] += (
                "请用已有结果给出 final；尚未完成的部分写入 gaps，不再启动新工具。"
            )
    return messages
