"""旧 JSON 决策协议的兼容循环，共用 Python 领域执行规则。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wonder_stockresearch import observability, progress, storage
from wonder_stockresearch.interactive import (
    agent_context,
    agent_execution,
    agent_memory,
    agent_model,
    agent_policy,
    agent_session,
    agent_tools,
)
from wonder_stockresearch.interactive.agent_errors import AgentError


def execute(
    root: Path,
    session_id: str,
    current_question: str,
    *,
    turn_id: str,
    attempt_id: str,
    model_client: agent_model.AgentModelClient,
    skill_context: str = "",
    tool_names: list[str] | tuple[str, ...] | None = None,
    on_progress: progress.ProgressCallback | None = None,
    tool_executor: agent_execution.ToolExecutor = agent_tools.execute_tool,
) -> dict[str, Any]:
    row = agent_session.get_session(root, session_id)
    turn = agent_session.get_turn(root, session_id, turn_id)
    runtime_row = {**row, "turn_id": turn_id}
    question = agent_session.session_question(row)
    budget = agent_execution.AttemptBudget.load(root, row, turn, attempt_id)
    context = agent_session.initial_context(root, runtime_row, current_question)
    context["turn_id"] = turn_id
    with storage.open_db(root) as connection:
        successful = agent_execution.successful_tool_calls(
            storage.list_agent_events(connection, session_id), turn_id
        )
    context["prior_successful_tool_calls"] = [
        {"arguments_hash": key, "result": agent_memory.compact(value, 1600)}
        for key, value in list(successful.items())[-6:]
    ]
    memory_ids = [
        item["memory_id"] for item in context.get("relevant_memory", []) if item.get("memory_id")
    ]
    if memory_ids:
        agent_session.append_event(
            root,
            session_id,
            "memory_selection",
            {"memory_ids": memory_ids},
            turn_id=turn_id,
            attempt_id=attempt_id,
        )
    tools = agent_tools.tool_catalog(tool_names)
    system = agent_policy.build_system_prompt(question.focus, tools, skill_context)
    state = agent_execution.ToolExecutionState(
        root=root,
        row=runtime_row,
        turn_id=turn_id,
        attempt_id=attempt_id,
        allowed_names=tuple(item["name"] for item in tools),
        tool_names=tool_names,
        question=current_question,
        successful=successful,
        working_memory=context.get("scratchpad"),
    )
    history = [
        agent_context.ContextMessage("user", current_question),
        agent_context.ContextMessage("background", context),
    ]
    while budget.iteration < budget.max_iterations:
        if budget.remaining <= 0:
            raise AgentError("agent_budget_exhausted", "Agent 已达到 token 预算")
        iteration = budget.iteration + 1
        progress.emit(
            on_progress,
            "agent",
            "running",
            f"正在进行第 {iteration}/{budget.max_iterations} 轮分析",
            session_id=session_id,
        )
        with observability.span(
            root,
            f"agent.iteration.{iteration}",
            span_kind="chain",
            attributes={
                "iteration": iteration,
                "max_iterations": budget.max_iterations,
                "used_tokens_before": budget.used_tokens,
                "token_budget": budget.token_budget,
            },
        ):
            remaining = budget.remaining
            output_reserve = min(1024, max(64, remaining // 4))
            planned_calls = min(3, budget.max_iterations - iteration + 1)
            fixed_input = agent_memory.estimate_input_tokens(
                [{"content": system}, {"content": current_question}]
            )
            latest = next((item for item in reversed(history) if item.kind == "tool_result"), None)
            result_reserve = (
                0
                if latest is None
                else min(
                    1200,
                    agent_memory.estimate_input_tokens(
                        [{"content": agent_memory.tool_context(latest.payload, token_limit=1200)}]
                    ),
                )
            )
            input_ceiling = min(
                agent_model.MAX_AGENT_INPUT_TOKENS,
                max(agent_model.AGENT_INPUT_TARGET_TOKENS, fixed_input + result_reserve + 428),
            )
            target = min(
                input_ceiling,
                remaining - output_reserve,
                max(remaining // planned_calls, fixed_input + result_reserve + 428),
            )
            messages = agent_memory.compress_messages(
                root,
                session_id,
                current_question,
                agent_context.convert_to_llm(
                    system,
                    agent_context.transform_context(history, state.working_memory),
                    max_input_tokens=max(0, target),
                    remaining_iterations=budget.max_iterations - iteration + 1,
                ),
                max_input_tokens=max(0, target),
            )
            request_budget = agent_model.remaining_request_tokens.set(budget.reserve())
            try:
                payload, consumed = model_client.decide(root, messages, str(turn["model"]))
            except AgentError as error:
                if error.consumed_tokens is not None:
                    budget.settle(error.consumed_tokens)
                raise
            finally:
                agent_model.remaining_request_tokens.reset(request_budget)
            budget.settle(consumed)
            decision = agent_session.parse_decision(payload)
            history.append(
                agent_context.ContextMessage("assistant", decision.model_dump(mode="json"))
            )
            with observability.span(
                root,
                "agent.decision",
                span_kind="decision",
                attributes=agent_session.decision_trace_attributes(
                    decision, iteration=iteration, consumed_tokens=consumed
                ),
            ):
                pass
        agent_session.append_event(
            root,
            session_id,
            "decision",
            {"iteration": iteration, "type": decision.type, "tool": decision.tool},
            turn_id=turn_id,
            attempt_id=attempt_id,
        )
        if decision.type in {"final", "ask_user"}:
            return agent_execution.accept_answer(
                root, row, budget, decision, current_question, tool_names, on_progress
            )
        call = agent_execution.prepare_tool(state, str(decision.tool), decision.arguments)
        progress.emit(
            on_progress,
            "agent.tool",
            "running",
            f"正在读取工具：{call.name}",
            session_id=session_id,
        )
        outcome = agent_execution.execute_tool(state, call, iteration, tool_executor)
        payload = outcome.data if outcome.status == "success" else json.loads(outcome.model_text)
        history.append(
            agent_context.ContextMessage(
                "tool_result" if outcome.status == "success" else "tool_error", payload, call.name
            )
        )
    raise AgentError("agent_iteration_exhausted", "Agent 已达到最大迭代次数")
