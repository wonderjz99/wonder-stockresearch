"""两种运行时共用的执行规则；Pi 与旧循环只负责各自的消息协议。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from wonder_stockresearch import observability, progress, storage
from wonder_stockresearch.interactive import (
    agent_memory,
    agent_results,
    agent_session,
    agent_tools,
    analysis_workspace,
    query_workspace,
)
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import AgentDecision, InteractiveReport


@dataclass
class AttemptBudget:
    """本次 Attempt 的显式预算状态；数据库仍是跨执行的权威记录。"""

    root: Path
    session_id: str
    turn_id: str
    attempt_id: str
    max_iterations: int
    token_budget: int
    iteration: int
    used_tokens: int
    session_initial_iteration: int
    session_initial_tokens: int
    attempt_iteration: int = 0
    attempt_used_tokens: int = 0
    reservation: int | None = None

    @classmethod
    def load(
        cls, root: Path, row: dict[str, Any], turn: dict[str, Any], attempt_id: str
    ) -> AttemptBudget:
        maximum, tokens = agent_session.budget_for_turn(turn)
        return cls(
            root,
            str(row["id"]),
            str(turn["id"]),
            attempt_id,
            maximum,
            tokens,
            int(turn["iteration"]),
            int(turn["used_tokens"]),
            int(row["iteration"]),
            int(row["used_tokens"]),
        )

    @property
    def remaining(self) -> int:
        return self.token_budget - self.used_tokens

    def totals(self) -> dict[str, int]:
        return {
            "turn_iteration": self.iteration,
            "turn_used_tokens": self.used_tokens,
            "attempt_iteration": self.attempt_iteration,
            "attempt_used_tokens": self.attempt_used_tokens,
            "session_iteration": self.session_initial_iteration + self.attempt_iteration,
            "session_used_tokens": self.session_initial_tokens + self.attempt_used_tokens,
        }

    def persist(self) -> None:
        agent_session.record_attempt_progress(
            self.root, self.session_id, self.turn_id, attempt_id=self.attempt_id, **self.totals()
        )

    def reserve(self) -> int:
        if self.reservation is not None:
            raise AgentError("model_usage_unavailable", "上次模型请求尚未结算，不能再次请求")
        if self.iteration >= self.max_iterations:
            raise AgentError("agent_iteration_exhausted", "Agent 已达到最大迭代次数")
        remaining = self.remaining
        if remaining <= 0:
            raise AgentError("agent_budget_exhausted", "Agent 已达到 token 预算")
        self.iteration += 1
        self.attempt_iteration += 1
        self.reservation = remaining
        self.used_tokens += remaining
        self.attempt_used_tokens += remaining
        self.persist()  # 必须在网络请求前提交；未知用量不会产生免费 retry。
        return remaining

    def settle(self, consumed: Any) -> None:
        if isinstance(consumed, bool) or not isinstance(consumed, int) or consumed < 0:
            raise AgentError("model_usage_unavailable", "模型用量无效，保留预留预算并停止")
        if self.reservation is None:
            raise AgentError("model_usage_unavailable", "模型请求没有待结算预算，不能重复结算")
        self.used_tokens += consumed - self.reservation
        self.attempt_used_tokens += consumed - self.reservation
        self.reservation = None
        self.persist()
        if self.used_tokens > self.token_budget:
            raise AgentError("agent_budget_exhausted", "本次模型用量超过 Turn 预算，未接受回答")


def successful_tool_calls(events: list[dict[str, Any]], turn_id: str) -> dict[str, dict[str, Any]]:
    """只恢复同一 Turn 已确认的调用；大结果保留其产物导航。"""
    successful = {}
    for event in events:
        if event.get("turn_id") != turn_id or event["event_type"] != "tool_result":
            continue
        try:
            saved = json.loads(event["payload_json"])
        except (ValueError, TypeError):
            continue
        result = saved.get("result")
        if not isinstance(result, dict):
            continue
        if saved.get("tool") == "fetch_financial_data" and not result.get("cached"):
            successful.clear()
        if saved.get("arguments_hash") and (
            not saved.get("result_truncated") or result.get("analysis_id")
        ):
            successful[saved["arguments_hash"]] = result
    return successful


@dataclass(frozen=True)
class PreparedTool:
    name: str
    arguments: dict[str, Any]
    arguments_hash: str


@dataclass
class ToolExecutionState:
    root: Path
    row: dict[str, Any]
    turn_id: str
    attempt_id: str
    allowed_names: tuple[str, ...]
    tool_names: list[str] | tuple[str, ...] | None
    question: str
    working_memory: dict[str, Any] | None = None
    successful: dict[str, dict[str, Any]] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    seen_errors: set[tuple[str, str, str]] = field(default_factory=set)
    repeats: int = 0
    failures: int = 0


ToolExecutor = Callable[
    [Path, dict[str, Any], str, object, list[str] | tuple[str, ...] | None], dict[str, Any]
]


def prepare_tool(state: ToolExecutionState, name: str, arguments: dict[str, Any]) -> PreparedTool:
    if name not in state.allowed_names:
        raise AgentError(
            "unauthorized_tool", f"当前 Skill/capability 未授权使用工具：{name}", exit_code=2
        )
    encoded = json.dumps(
        {"tool": name, "arguments": arguments}, ensure_ascii=False, sort_keys=True, default=str
    )
    key = hashlib.sha256(encoded.encode()).hexdigest()
    if key in state.seen:
        state.repeats += 1
        if key not in state.successful or state.repeats >= 2:
            raise AgentError("repeated_tool_call", "Agent 连续重复工具调用，已停止当前 Turn")
    else:
        state.repeats = 0
    state.seen.add(key)
    return PreparedTool(name, arguments, key)


def execute_tool(
    state: ToolExecutionState,
    call: PreparedTool,
    iteration: int,
    executor: ToolExecutor = agent_tools.execute_tool,
) -> agent_results.ToolOutcome:
    """执行一次受控工具，先确认完整结果/回执，再生成有界模型视图。"""
    session_id = str(state.row["id"])
    try:
        with observability.span(
            state.root,
            f"tool.{call.name}",
            span_kind="tool",
            attributes={
                "tool": call.name,
                "iteration": iteration,
                "arguments_sha256": call.arguments_hash,
                "argument_count": len(call.arguments),
                "argument_keys": sorted(call.arguments),
            },
        ) as tool_span:
            result = state.successful.get(call.arguments_hash)
            reused = result is not None
            if result is None:
                result = executor(
                    state.root, state.row, call.name, call.arguments, state.tool_names
                )
            compact = agent_memory.compact(result)
            tool_span.set_attributes(
                {
                    "returned_type": type(result).__name__,
                    "returned_chars": len(compact),
                    "returned_sha256": hashlib.sha256(compact.encode()).hexdigest(),
                }
            )
            observability.export_tool_io(tool_span, arguments=call.arguments, result=result)
    except (
        query_workspace.QueryWorkspaceError,
        analysis_workspace.AnalysisWorkspaceError,
        AgentError,
    ) as error:
        if isinstance(error, AgentError) and error.code != "invalid_tool_arguments":
            raise
        error_key = (call.name, error.code, call.arguments_hash)
        if error_key in state.seen_errors:
            raise AgentError(
                "repeated_analysis_error", "Agent 重复触发相同工具错误，已停止当前 Turn"
            ) from error
        state.seen_errors.add(error_key)
        state.failures += 1
        agent_session.append_event(
            state.root,
            session_id,
            "tool_error",
            {
                "iteration": iteration,
                "tool": call.name,
                "code": error.code,
            },
            turn_id=state.turn_id,
            attempt_id=state.attempt_id,
        )
        if state.failures >= 3:
            raise AgentError(
                "analysis_tool_failures", "连续三次工具错误，已停止当前 Turn"
            ) from error
        return agent_results.ToolOutcome(
            status="error",
            error=agent_results.ToolFailure(
                code=error.code,
                message=error.message,
                recoverable=True,
                next_step_hint=agent_results.recovery_hint(error.code),
            ),
        )
    state.failures = 0
    if call.name == "fetch_financial_data" and not reused and not result.get("cached"):
        state.successful.clear()
        state.seen = {call.arguments_hash}
        state.repeats = 0
    query_workspace.record_tool_evidence_receipts(
        state.root, state.row, state.turn_id, call.name, result
    )
    state.successful[call.arguments_hash] = result
    state.working_memory = agent_memory.record_tool_observation(
        state.root, session_id, state.question, call.name, result
    )
    encoded = json.dumps(result, ensure_ascii=False, default=str)
    truncated = len(encoded) > 7000
    stored = (
        result
        if not truncated
        else {
            "analysis_id": result.get("analysis_id"),
            "status": result.get("status"),
            "artifact_summary_only": True,
            "result_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "preview": agent_memory.compact(result, 3000),
        }
    )
    agent_session.append_event(
        state.root,
        session_id,
        "tool_result",
        {
            "turn_id": state.turn_id,
            "iteration": iteration,
            "tool": call.name,
            "arguments_hash": call.arguments_hash,
            "result": stored,
            "result_truncated": truncated,
        },
        turn_id=state.turn_id,
        attempt_id=state.attempt_id,
    )
    if state.repeats:
        result = {
            **result,
            "cached": True,
            "next_step_hint": "相同调用已成功，请继续回答或计算，不要再重复。",
        }
    return agent_results.ToolOutcome(status="success", data=result)


def accept_answer(
    root: Path,
    row: dict[str, Any],
    budget: AttemptBudget,
    decision: AgentDecision,
    question: str,
    tool_names: list[str] | tuple[str, ...] | None,
    on_progress: progress.ProgressCallback | None = None,
) -> dict[str, Any]:
    if budget.reservation is not None:
        raise AgentError("model_usage_unavailable", "模型用量尚未结算，不能接受回答")
    report = None
    if decision.type == "final":
        refs = [
            *decision.evidence_refs,
            *[ref for claim in decision.claims for ref in claim.evidence],
        ]
        agent_session.validate_evidence_refs(
            root,
            row,
            refs,
            turn_id=budget.turn_id,
            require_turn_receipts=tool_names is not None and "run_readonly_sql" in tool_names,
        )
        report = InteractiveReport(
            session_id=UUID(budget.session_id),
            ticker=str(row["ticker"]),
            user_question=question,
            answer=str(decision.answer),
            claims=decision.claims,
            evidence_refs=decision.evidence_refs,
            counterarguments=decision.counterarguments,
            gaps=decision.gaps,
            suggested_next_checks=decision.suggested_next_checks,
            created_at=storage.now_utc(),
        )
    elif decision.type != "ask_user":
        raise AgentError("invalid_decision", "工具调用不能作为最终回答提交")
    status = "succeeded" if report is not None else "waiting_user"
    agent_session.finish_attempt_and_turn(
        root,
        budget.session_id,
        turn_id=budget.turn_id,
        attempt_id=budget.attempt_id,
        status=status,
        result=report,
        **budget.totals(),
    )
    response = {
        "session_id": budget.session_id,
        "turn_id": budget.turn_id,
        "ticker": row["ticker"],
        "run_id": row["run_id"],
    }
    if report is not None:
        agent_memory.complete_scratchpad(root, budget.session_id, question)
        progress.emit(
            on_progress,
            "agent",
            "succeeded",
            "交互分析完成，已生成临时报告",
            session_id=budget.session_id,
        )
        return {
            **response,
            "status": "completed",
            "turn_status": status,
            "report": json.loads(report.model_dump_json()),
        }
    progress.emit(
        on_progress, "agent", "waiting_user", "Agent 需要用户补充信息", session_id=budget.session_id
    )
    return {**response, "status": status, "question": decision.question}
