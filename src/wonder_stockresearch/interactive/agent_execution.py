"""两种运行时共用的执行规则；Pi 与旧循环只负责各自的消息协议。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from wonder_stockresearch import observability, progress, storage
from wonder_stockresearch.interactive import (
    agent_company,
    agent_memory,
    agent_results,
    agent_session,
    agent_tools,
    agent_working_record,
    analysis_workspace,
    numeric_answer,
    query_workspace,
    question_requirements,
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
    blocked: agent_results.ToolOutcome | None = None


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
    correction_used: bool = False
    final_only: bool = False
    coverage_gaps: set[str] = field(default_factory=set)


def restore_tool_state(state: ToolExecutionState) -> None:
    with storage.open_db(state.root) as connection:
        events = storage.list_agent_events(connection, str(state.row["id"]))
    for event in events:
        if event.get("turn_id") != state.turn_id:
            continue
        try:
            payload = json.loads(event["payload_json"])
        except (ValueError, TypeError):
            if event["event_type"] in {"tool_error", "tool_blocked", "tool_final_only"}:
                raise AgentError(
                    "invalid_execution_record", "恢复记录损坏，不能确认纠正额度"
                ) from None
            continue  # 兼容历史被截断的成功结果，不将其恢复为成功缓存。
        key = payload.get("arguments_hash")
        if key and event["event_type"] in {"tool_error", "tool_result", "tool_blocked"}:
            state.seen.add(key)
        if event["event_type"] == "tool_error":
            state.failures += 1
            if key:
                state.seen_errors.add((payload.get("tool", ""), payload.get("code", ""), key))
        if event["event_type"] == "tool_blocked" and payload.get("correction_granted"):
            state.correction_used = True
        if event["event_type"] == "tool_final_only":
            state.final_only = True
        if (
            event["event_type"] == "tool_result"
            and payload.get("tool") == "fetch_financial_data"
            and not payload.get("result", {}).get("cached")
        ):
            state.coverage_gaps.clear()
        diagnostic = payload.get("working_observation", payload.get("result", {})).get(
            "coverage_diagnostic", {}
        )
        if diagnostic.get("period_kind_unavailable"):
            state.coverage_gaps.add(diagnostic["requested_period_kind"])
    state.working_memory = agent_working_record.build(events, state.turn_id, state.question)


def refresh_working_record(state: ToolExecutionState) -> None:
    with storage.open_db(state.root) as connection:
        events = storage.list_agent_events(connection, str(state.row["id"]))
    state.working_memory = agent_working_record.build(events, state.turn_id, state.question)


def finish_tools(state: ToolExecutionState, reason: str) -> None:
    if not state.final_only:
        state.final_only = True
        agent_session.append_event(
            state.root,
            str(state.row["id"]),
            "tool_final_only",
            {"code": reason},
            turn_id=state.turn_id,
            attempt_id=state.attempt_id,
        )
        refresh_working_record(state)


def _blocked_tool(state: ToolExecutionState, name: str, arguments: dict, key: str) -> PreparedTool:
    granted = not state.correction_used
    state.correction_used = True
    code = "repeated_tool_call" if granted else "tool_recovery_exhausted"
    if not granted:
        finish_tools(state, code)
    failure = agent_results.ToolFailure(
        code=code,
        message="相同请求已处理，本次阻止执行；请修改请求或形成带缺口的回答",
        recoverable=True,
        executed=False,
        analysis_id=arguments.get("analysis_id")
        if isinstance(arguments.get("analysis_id"), str)
        else None,
        next_step_hint=agent_results.recovery_hint(code),
    )
    agent_session.append_event(
        state.root,
        str(state.row["id"]),
        "tool_blocked",
        {
            "tool": name,
            "arguments_hash": key,
            "correction_granted": granted,
            "final_only": state.final_only,
            **failure.model_dump(exclude_none=True),
        },
        turn_id=state.turn_id,
        attempt_id=state.attempt_id,
    )
    with observability.span(
        state.root,
        "tool.blocked",
        span_kind="guardrail",
        attributes={
            "tool": name,
            "code": code,
            "outcome": "blocked",
            "executed": False,
            "arguments_sha256": key,
        },
    ):
        pass
    refresh_working_record(state)
    return PreparedTool(
        name, arguments, key, agent_results.ToolOutcome(status="error", error=failure)
    )


ToolExecutor = Callable[
    [Path, dict[str, Any], str, object, list[str] | tuple[str, ...] | None], dict[str, Any]
]


def can_repair_answer(error: AgentError) -> bool:
    return error.code.startswith("numeric_") or error.code in {
        "invalid_decision",
        "unknown_source_reference",
        "unlinked_source_reference",
        "missing_turn_evidence_receipt",
        "unknown_numeric_fact",
        "unknown_numeric_analysis",
        "missing_numeric_receipt",
        "ambiguous_numeric_series",
        "task_period_mismatch",
        "unknown_reference_id",
        "invalid_reference_id",
    }


def answer_repair_used(root: Path, turn_id: str) -> bool:
    with storage.open_db(root) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM agent_events WHERE turn_id=? AND event_type='answer_repair' LIMIT 1",
                (turn_id,),
            ).fetchone()
            is not None
        )


def answer_repair_message(root: Path, turn_id: str, error: AgentError) -> dict:
    with storage.open_db(root) as connection:
        receipts = storage.list_agent_evidence_receipts(connection, turn_id)
    return {
        "code": error.code,
        "message": error.message,
        "next_step_hint": "预算内纠正一次最终提交；只用已取得的事实/成功产物。引用用以下完整定位；数值用 numeric_blocks；未完成部分列 gaps，不重跑成功采集。",
        "available_refs": [
            {k: r[k] for k in ("source_id", "locator", "fact_id", "analysis_id")}
            for r in receipts[:12]
        ],
    }


def prepare_answer(
    root: Path, row: dict, turn_id: str, decision: AgentDecision, tool_names
) -> tuple:
    task = question_requirements.load(root, row, turn_id)
    if question_requirements.question(task, final_check=True):
        return str(decision.answer), [], [], task
    if task and decision.task and decision.task.periods and task.get("target_years"):
        proposed = question_requirements.explicit_years(" ".join(decision.task.periods))
        if proposed != task["target_years"]:
            raise AgentError(
                "task_period_mismatch", "提交的目标期间与用户指定/确认期间不一致", exit_code=2
            )
    answer, blocks, generated_refs = numeric_answer.prepare(root, row, turn_id, decision, task)
    refs = [
        *decision.evidence_refs,
        *[ref for claim in decision.claims for ref in claim.evidence],
        *generated_refs,
    ]
    agent_session.validate_evidence_refs(
        root,
        row,
        refs,
        turn_id=turn_id,
        require_turn_receipts=tool_names is not None and "run_readonly_sql" in tool_names,
    )
    return answer, blocks, generated_refs, task


def prepare_tool(state: ToolExecutionState, name: str, arguments: dict[str, Any]) -> PreparedTool:
    if name not in state.allowed_names:
        raise AgentError(
            "unauthorized_tool", f"当前 Skill/capability 未授权使用工具：{name}", exit_code=2
        )
    if state.final_only:
        raise AgentError("invalid_decision", "当前已进入无工具收尾；只能回答已有事实和缺口")
    canonical = dict(arguments)
    if name == "query_company_data" and isinstance(arguments.get("query"), str):
        canonical["query"] = sorted(agent_company.search_terms(arguments["query"]))
    encoded = json.dumps(
        {"tool": name, "arguments": canonical}, ensure_ascii=False, sort_keys=True, default=str
    )
    key = hashlib.sha256(encoded.encode()).hexdigest()
    if name in {"run_analysis_function", "run_analysis_code", "run_trusted_calculation"}:
        task = question_requirements.load(state.root, state.row, state.turn_id)
        if question_requirements.question(task, final_check=True):
            return PreparedTool(
                name,
                arguments,
                key,
                agent_results.ToolOutcome(
                    status="error",
                    error=agent_results.ToolFailure(
                        code="question_needs_confirmation",
                        message="影响计算范围的选择尚未确认；可以只读定位财年/覆盖，再用 ask_user 提出具体选项。",
                        recoverable=True,
                        executed=False,
                        next_step_hint="先确认用户目标，不能按可得数据代替用户选择。",
                    ),
                ),
            )
    kind = arguments.get("period_kind") if name == "query_company_data" else None
    if name == "run_readonly_sql" and isinstance(arguments.get("sql"), str):
        match = re.search(r"\bperiod_kind\s*=\s*'([^']+)'", arguments["sql"], re.IGNORECASE)
        kind = match.group(1) if match else None
    if kind in state.coverage_gaps:
        failure = agent_results.ToolFailure(
            code="known_coverage_gap",
            message=f"本 Turn 授权归档已确认没有 {kind} 类型的已解析事实；本次相同范围不再执行空查询。",
            recoverable=True,
            executed=False,
            next_step_hint="保留用户目标；可改用已授权的其他来源/补取路径，否则说明本次归档缺口。不能推断公司未披露，也不要再次查询同范围相同期间类型。",
        )
        agent_session.append_event(
            state.root,
            str(state.row["id"]),
            "tool_blocked",
            {"tool": name, "arguments_hash": key, **failure.model_dump(exclude_none=True)},
            turn_id=state.turn_id,
            attempt_id=state.attempt_id,
        )
        return PreparedTool(
            name, arguments, key, agent_results.ToolOutcome(status="error", error=failure)
        )
    if key in state.seen:
        state.repeats += 1
        if key not in state.successful or state.repeats >= 2:
            return _blocked_tool(state, name, arguments, key)
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
    if call.blocked is not None:
        return call.blocked
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
                try:
                    result = executor(
                        state.root, state.row, call.name, call.arguments, state.tool_names
                    )
                except (
                    query_workspace.QueryWorkspaceError,
                    analysis_workspace.AnalysisWorkspaceError,
                    AgentError,
                ) as error:
                    observability.export_tool_io(
                        tool_span,
                        arguments=call.arguments,
                        result={"is_error": True, "code": error.code, "message": error.message},
                    )
                    raise
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
        if error.code in {
            "unknown_analysis_run",
            "unknown_agent_turn",
            "turn_run_mismatch",
            "analysis_result_missing",
            "analysis_result_invalid",
        }:
            raise AgentError(error.code, error.message, exit_code=error.exit_code) from error
        error_key = (call.name, error.code, call.arguments_hash)
        if error_key in state.seen_errors:
            blocked = _blocked_tool(state, call.name, call.arguments, call.arguments_hash).blocked
            assert blocked is not None
            return blocked
        state.seen_errors.add(error_key)
        state.failures += 1
        if state.failures >= 3:
            finish_tools(state, "analysis_tool_failures")
        executed = error.code not in {
            "analysis_function_not_allowed",
            "invalid_analysis_function",
            "invalid_tool_arguments",
            "analysis_function_result_missing",
            "analysis_sql_not_allowed",
            "invalid_analysis_sql",
        }
        failure = agent_results.ToolFailure(
            code=error.code,
            message=error.message,
            recoverable=True,
            executed=executed,
            analysis_id=call.arguments.get("analysis_id")
            if isinstance(call.arguments.get("analysis_id"), str)
            else None,
            node=getattr(error, "node", None),
            line=getattr(error, "line", None),
            column=getattr(error, "column", None),
            next_step_hint=agent_results.recovery_hint(
                "analysis_tool_failures" if state.final_only else error.code
            ),
        )
        agent_session.append_event(
            state.root,
            session_id,
            "tool_error",
            {
                "iteration": iteration,
                "tool": call.name,
                "arguments_hash": call.arguments_hash,
                "final_only": state.final_only,
                **failure.model_dump(exclude_none=True),
            },
            turn_id=state.turn_id,
            attempt_id=state.attempt_id,
        )
        refresh_working_record(state)
        return agent_results.ToolOutcome(status="error", error=failure)
    if call.name == "fetch_financial_data" and not reused and not result.get("cached"):
        state.successful.clear()
        state.seen = {call.arguments_hash}
        state.repeats = 0
        state.coverage_gaps.clear()
    query_workspace.record_tool_evidence_receipts(
        state.root, state.row, state.turn_id, call.name, result
    )
    diagnostic = result.get("coverage_diagnostic", {})
    if diagnostic.get("period_kind_unavailable"):
        state.coverage_gaps.add(diagnostic["requested_period_kind"])
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
            "working_observation": agent_working_record.summarize_result(call.name, result),
        },
        turn_id=state.turn_id,
        attempt_id=state.attempt_id,
    )
    refresh_working_record(state)
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
    task = question_requirements.load(root, row, budget.turn_id)
    if decision.type == "final" and (
        pending := question_requirements.question(task, final_check=True)
    ):
        decision = decision.model_copy(update={"type": "ask_user", "question": pending})
    if decision.type == "final" and decision.task and decision.task.unresolved:
        decision = decision.model_copy(
            update={
                "type": "ask_user",
                "question": "这些选择会影响答案，请确认：" + "；".join(decision.task.unresolved),
            }
        )
    if decision.type == "ask_user" and task and decision.task and decision.task.periods:
        proposed = question_requirements.explicit_years(" ".join(decision.task.periods))
        visible = question_requirements.explicit_years(str(decision.question))
        if proposed and proposed == visible:
            task["pending_periods"] = decision.task.periods
            task["unresolved"] = task.get("unresolved") or ["period_range"]
            question_requirements.save(root, row, budget.turn_id, task)
    if decision.type == "final":
        try:
            answer, blocks, generated_refs, task = prepare_answer(
                root, row, budget.turn_id, decision, tool_names
            )
        except AgentError as error:
            if error.code != "numeric_coverage_needs_confirmation":
                raise
            return accept_answer(
                root,
                row,
                budget,
                AgentDecision(type="ask_user", question=error.message),
                question,
                tool_names,
                on_progress,
            )
        report = InteractiveReport(
            session_id=UUID(budget.session_id),
            ticker=str(row["ticker"]),
            user_question=question,
            answer=answer,
            claims=decision.claims,
            evidence_refs=[
                *decision.evidence_refs,
                *[r for r in generated_refs if r not in decision.evidence_refs],
            ],
            numeric_blocks=blocks,
            task_requirements=task,
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
