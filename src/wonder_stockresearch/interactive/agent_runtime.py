"""交互 Agent 的小型运行时约束。

这里刻意只定义 Turn/Attempt 的生命周期校验，不承担队列、调度或通用工作流
引擎职责。公开入口在 :mod:`wonder_stockresearch.agent`，实际循环由兼容
适配器或 Pi 适配器运行，共用执行规则在 interactive.agent_execution。
"""

from __future__ import annotations


class RuntimeTransitionError(ValueError):
    """请求的 Turn 状态转换不符合交互运行时约束。"""


TURN_TERMINAL_STATUSES = frozenset({"succeeded", "budget_exhausted"})
_TURN_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"running", "interrupted", "budget_exhausted"}),
    "legacy": frozenset({"running", "interrupted", "budget_exhausted"}),
    "running": frozenset(
        {"waiting_user", "succeeded", "failed", "budget_exhausted", "interrupted"}
    ),
    "waiting_user": frozenset({"running", "interrupted", "budget_exhausted"}),
    "failed": frozenset({"running", "interrupted", "budget_exhausted"}),
    "interrupted": frozenset({"running", "budget_exhausted"}),
    "succeeded": frozenset(),
    "budget_exhausted": frozenset(),
}


def check_turn_transition(current: str, target: str) -> None:
    """拒绝会改写已完成 Turn 或绕开等待/失败状态的转换。"""
    if target not in _TURN_TRANSITIONS.get(current, frozenset()):
        raise RuntimeTransitionError(f"Turn 不能从 {current!r} 转为 {target!r}")


def retryable_turn_status(status: str) -> bool:
    """失败或中断的 Turn 可以新建 Attempt；预算耗尽不可以。"""
    return status in {"failed", "interrupted"}
