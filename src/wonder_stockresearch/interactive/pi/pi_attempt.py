"""Pi 生命周期到 Python 业务操作的适配，状态显式保存，不拥有业务账本。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wonder_stockresearch import observability, progress, storage
from wonder_stockresearch.interactive import (
    agent_execution,
    agent_model,
    agent_results,
    agent_session,
    agent_tools,
    agent_working_record,
)
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.interactive.pi import pi_context, pi_evidence
from wonder_stockresearch.models import AgentDecision


@dataclass
class PiAttempt:
    root: Path
    row: dict[str, Any]
    turn: dict[str, Any]
    budget: agent_execution.AttemptBudget
    tool_state: agent_execution.ToolExecutionState
    system: str
    tools: list[dict[str, Any]]
    model_client: agent_model.AgentModelClient | None
    on_progress: progress.ProgressCallback | None = None
    tool_executor: agent_execution.ToolExecutor = agent_tools.execute_tool
    candidate: AgentDecision | None = None
    repair_count: int = 0
    pending_tools: dict[str, agent_execution.PreparedTool] = field(default_factory=dict)
    native_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    available_refs: dict[str, Any] = field(default_factory=dict)
    model_span: Any = None
    request_span: Any = None
    request_record: Any = None

    @property
    def input_limit(self) -> int:
        return int(
            json.loads(self.turn["budget_json"]).get(
                "input_token_limit", agent_model.MAX_AGENT_INPUT_TOKENS
            )
        )

    @property
    def output_limit(self) -> int:
        return int(
            json.loads(self.turn["budget_json"]).get(
                "output_token_limit", agent_model.MAX_AGENT_OUTPUT_TOKENS
            )
        )

    def handle(self, kind: str, payload: dict[str, Any]) -> Any:
        handlers = {
            "context": self.context,
            "model_start": self.begin_model,
            "scripted_model": self.scripted_model,
            "model_end": self.end_model,
            "tool_check": self.check_tool,
            "tool": self.run_tool,
            "message": self.message,
            "lifecycle": self.lifecycle,
            "settled": self.settled,
        }
        handler = handlers.get(kind)
        if handler is None:
            raise AgentError("pi_protocol_error", "Pi runtime 返回未知事件")
        return handler(payload)

    def save_message(self, message: dict[str, Any]) -> None:
        with (
            storage.project_lock(self.root),
            storage.open_db(self.root) as connection,
            storage.transaction(connection),
        ):
            storage.append_agent_message(
                connection,
                session_id=self.budget.session_id,
                turn_id=self.budget.turn_id,
                attempt_id=self.budget.attempt_id,
                message=pi_context.visible(message),
            )

    def context(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        remaining = self.budget.remaining
        reserve = min(self.output_limit, max(64, remaining // 4))
        target = min(self.input_limit, max(8000, remaining // 3), max(0, remaining - reserve))
        messages = payload["messages"]
        if any(item["role"] == "toolResult" for item in messages):
            messages = [
                {**item, "content": pi_context.text(item).split("\n辅助上下文", 1)[0]}
                if item["role"] == "user"
                else item
                for item in messages
            ]
        remaining_calls = self.budget.max_iterations - self.budget.iteration
        if remaining_calls == 1:
            agent_execution.finish_tools(self.tool_state, "last_iteration")
        hint = f"运行状态：含本次还剩 {remaining_calls} 次决策；剩余累计预算 {remaining} tokens。"
        if remaining_calls == 1:
            hint += "请用已有结果给出 final；未完成部分写入 gaps，不再启动新工具。"
        if self.tool_state.final_only:
            hint += "纠正机会/工具轮次已结束；本轮无工具，必须按已有事实和缺口回答。"
        extra = []
        memory = json.dumps(
            agent_working_record.project(self.tool_state.working_memory or {}), ensure_ascii=False
        )
        if memory:
            extra.append({"role": "user", "content": "本 Turn 工作记录：" + memory, "timestamp": 0})
        extra.append({"role": "user", "content": hint, "timestamp": 0})
        projected = pi_context.project(
            messages,
            self.system,
            [] if self.repair_count or self.tool_state.final_only else self.tools,
            max(0, target - pi_context.estimate("", [], extra)),
        )
        return [*projected, *extra]

    def begin_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        remaining = self.budget.remaining
        if self.budget.iteration >= self.budget.max_iterations:
            raise AgentError("agent_iteration_exhausted", "Agent 已达到最大迭代次数")
        if remaining <= 0:
            raise AgentError("agent_budget_exhausted", "Agent 已达到 token 预算")
        estimated = pi_context.estimate(
            self.system,
            [] if self.repair_count or self.tool_state.final_only else self.tools,
            payload["messages"],
        )
        if self.model_client is None and estimated >= remaining:
            raise AgentError(
                "agent_budget_exhausted",
                f"剩余预算 {remaining} tokens，输入估算 {estimated} tokens；请缩小问题",
            )
        if self.model_client is None and estimated > self.input_limit:
            raise AgentError(
                "agent_context_too_large",
                f"压缩后输入估算 {estimated} tokens，超过单轮上限 {self.input_limit}；请缩小问题或分段查询",
            )
        self.budget.reserve()
        iteration = self.budget.iteration
        progress.emit(
            self.on_progress,
            "agent",
            "running",
            f"正在进行第 {iteration}/{self.budget.max_iterations} 轮分析",
            session_id=self.budget.session_id,
        )
        self.model_span = observability.span(
            self.root,
            f"agent.iteration.{iteration}",
            span_kind="chain",
            attributes={"iteration": iteration, "runtime": "pi"},
        )
        self.model_span.__enter__()
        if self.model_client is None:
            self.request_span = observability.span(
                self.root,
                "deepseek.chat.completions",
                span_kind="llm",
                attributes={
                    "provider": "deepseek",
                    "model": str(self.turn["model"]),
                    "runtime": "pi",
                    "kind": "agent_decision",
                    "llm.model_name": str(self.turn["model"]),
                    "llm.provider": "deepseek",
                },
            )
            self.request_record = self.request_span.__enter__()
            agent_session.append_event(
                self.root,
                self.budget.session_id,
                "context_budget",
                {
                    "iteration": iteration,
                    "estimated_input_tokens": estimated,
                    "input_token_limit": self.input_limit,
                    "remaining_tokens": remaining,
                    "repair_only": bool(self.repair_count),
                },
                turn_id=self.budget.turn_id,
                attempt_id=self.budget.attempt_id,
            )
        return {
            "max_tokens": min(self.output_limit, max(1, remaining - estimated)),
            "repair_only": bool(self.repair_count or self.tool_state.final_only),
        }

    def scripted_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.model_client is None or self.budget.reservation is None:
            raise AgentError("pi_protocol_error", "没有已许可的脚本模型请求")
        context_token = agent_model.remaining_request_tokens.set(self.budget.reservation)
        try:
            decision, consumed = self.model_client.decide(
                self.root,
                pi_context.scripted_messages(self.system, payload["messages"]),
                str(self.turn["model"]),
            )
        except AgentError as error:
            if error.consumed_tokens is not None:
                self.budget.settle(error.consumed_tokens)
            raise
        finally:
            agent_model.remaining_request_tokens.reset(context_token)
        return {"decision": decision, "consumed": consumed}

    def end_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.request_record is not None:
            usage = payload["message"].get("usage", {})
            input_tokens = sum(usage.get(key, 0) for key in ("input", "cacheRead", "cacheWrite"))
            self.request_record.set_attributes(
                {
                    "usage_known": bool(payload.get("usageKnown")),
                    "total_tokens": payload.get("consumed"),
                    "input_tokens": input_tokens,
                    "completion_tokens": usage.get("output", 0),
                    "llm.token_count.total": payload.get("consumed"),
                    "llm.token_count.prompt": input_tokens,
                    "llm.token_count.completion": usage.get("output", 0),
                }
            )
        if not payload.get("usageKnown"):
            raise AgentError("model_usage_unavailable", "模型未返回用量，保留预留预算并停止")
        self.budget.settle(payload.get("consumed"))
        message = pi_context.visible(payload["message"])
        self.save_message(message)
        if message.get("stopReason") in {"error", "aborted"}:
            raise AgentError("model_request_failed", "Pi 模型请求失败，已保留用量")
        if self.request_span is not None:
            self.request_span.__exit__(None, None, None)
            self.request_span = self.request_record = None
        calls = [item for item in message["content"] if item["type"] == "toolCall"]
        if calls:
            if self.repair_count or self.tool_state.final_only:
                raise AgentError("invalid_decision", "引用修复/收尾轮不能重新调用工具")
            for call in calls:
                if call["name"] not in self.tool_state.allowed_names:
                    raise AgentError(
                        "tool_not_allowed", f"当前 Turn 未授权工具：{call['name']}", exit_code=2
                    )
                if not isinstance(call["arguments"], dict):
                    raise AgentError("invalid_tool_arguments", "工具参数必须是对象")
                self.native_calls[call["id"]] = call
            decision = agent_session.parse_decision(
                {"type": "tool_call", "tool": calls[0]["name"], "arguments": calls[0]["arguments"]}
            )
        else:
            try:
                decision = agent_session.parse_decision(
                    pi_evidence.expand_decision(
                        json.loads(pi_context.text(message)), self.available_refs
                    )
                )
                if decision.type == "tool_call":
                    raise AgentError("invalid_decision", "Pi 工具调用必须使用原生 tool calls")
                if decision.type == "final" and not (decision.task and decision.task.unresolved):
                    agent_execution.prepare_answer(
                        self.root,
                        self.row,
                        self.budget.turn_id,
                        decision,
                        self.tool_state.tool_names,
                    )
            except (ValueError, AgentError) as error:
                if (
                    isinstance(error, AgentError)
                    and error.code == "numeric_coverage_needs_confirmation"
                ):
                    decision = AgentDecision(type="ask_user", question=error.message)
                    self.candidate = decision
                    self.close_model_span()
                    return {}
                if (
                    self.repair_count == 0
                    and not agent_execution.answer_repair_used(self.root, self.budget.turn_id)
                    and self.budget.remaining > 0
                    and self.budget.iteration < self.budget.max_iterations
                    and (
                        not isinstance(error, AgentError)
                        or agent_execution.can_repair_answer(error)
                    )
                ):
                    self.repair_count += 1
                    agent_session.append_event(
                        self.root,
                        self.budget.session_id,
                        "answer_repair",
                        {"code": getattr(error, "code", "invalid_decision")},
                        turn_id=self.budget.turn_id,
                        attempt_id=self.budget.attempt_id,
                    )
                    self.close_model_span()
                    return {
                        "repair": pi_evidence.repair_message(error, self.available_refs)
                        + (
                            "\n"
                            + json.dumps(
                                agent_execution.answer_repair_message(
                                    self.root, self.budget.turn_id, error
                                ),
                                ensure_ascii=False,
                            )
                            if isinstance(error, AgentError)
                            else ""
                        )
                    }
                if isinstance(error, AgentError):
                    raise
                raise AgentError("invalid_decision", "Agent 决策不是合法 JSON") from error
            self.candidate = decision
        with observability.span(
            self.root,
            "agent.decision",
            span_kind="decision",
            attributes=agent_session.decision_trace_attributes(
                decision, iteration=self.budget.iteration, consumed_tokens=payload["consumed"]
            ),
        ):
            pass
        self.close_model_span()
        agent_session.append_event(
            self.root,
            self.budget.session_id,
            "decision",
            {
                "iteration": self.budget.iteration,
                "type": decision.type,
                "tool": decision.tool,
            },
            turn_id=self.budget.turn_id,
            attempt_id=self.budget.attempt_id,
        )
        return {}

    def close_model_span(self) -> None:
        if self.model_span is not None:
            self.model_span.__exit__(None, None, None)
            self.model_span = None

    def check_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        call_id = payload["toolCallId"]
        if call_id in self.pending_tools:
            raise AgentError("pi_protocol_error", "同一个工具调用不能重复准备")
        if self.repair_count or self.budget.reservation is not None:
            raise AgentError("pi_protocol_error", "当前模型请求尚未结算或已进入无工具修复")
        self.pending_tools[call_id] = agent_execution.prepare_tool(
            self.tool_state, payload["name"], payload["arguments"]
        )
        return {}

    def run_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        call_id = payload["toolCallId"]
        call = self.pending_tools.pop(call_id, None)
        if call is None:  # 保留 Python scripted bridge 的直接工具注入接口。
            self.check_tool(payload)
            call = self.pending_tools.pop(call_id)
        if call.name != payload["name"] or call.arguments != payload["arguments"]:
            raise AgentError("pi_protocol_error", "工具执行与已准备调用不一致")
        outcome = agent_execution.execute_tool(
            self.tool_state, call, self.budget.iteration, self.tool_executor
        )
        self.native_calls.pop(call_id, None)
        if outcome.status == "success":
            self.available_refs = pi_evidence.catalog(self.root, self.budget.turn_id)
            outcome = agent_results.ToolOutcome(
                status="success", data=pi_evidence.add_reference_ids(outcome.data)
            )
        return {
            "content": outcome.model_text,
            "details": {"artifact_summary_only": True},
            "error": outcome.error.model_dump() if outcome.error else None,
        }

    def message(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = payload["message"]
        self.save_message(message)
        call = self.native_calls.pop(message["toolCallId"], None)
        if message.get("isError") and call is not None:
            # SDK 的 schema 校验可在 beforeToolCall 前拒绝参数；也纳入公共错误/恢复计数。
            prepared = self.pending_tools.pop(message["toolCallId"], None)
            if prepared is None:
                prepared = agent_execution.prepare_tool(
                    self.tool_state, call["name"], call["arguments"]
                )

            def reject_native_arguments(*_args):
                raise AgentError(
                    "invalid_tool_arguments", "工具参数未通过原生校验，请按工具 schema 修改"
                )

            agent_execution.execute_tool(
                self.tool_state, prepared, self.budget.iteration, reject_native_arguments
            )
        return {}

    def lifecycle(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload["type"] == "tool_execution_start":
            progress.emit(
                self.on_progress,
                "agent.tool",
                "running",
                f"正在处理工具：{payload['toolName']}",
                session_id=self.budget.session_id,
            )
        return {}  # 原生运行事件不是新的业务事件，更不能提交业务完成。

    def settled(self, _payload: dict[str, Any]) -> dict[str, Any]:
        if self.candidate is None or self.pending_tools or self.budget.reservation is not None:
            raise AgentError("invalid_decision", "Pi 未返回已结算的有效 final 或 ask_user 决策")
        return {}

    def close(self) -> None:
        if self.request_span is not None:
            self.request_span.__exit__(*sys.exc_info())
        if self.model_span is not None:
            self.model_span.__exit__(*sys.exc_info())
