"""配置项目内 Pi Agent；Python 业务操作与 Pi 生命周期适配各自独立。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wonder_stockresearch import envfile, progress, storage
from wonder_stockresearch.interactive import (
    agent_execution,
    agent_memory,
    agent_model,
    agent_policy,
    agent_session,
    agent_tools,
)
from wonder_stockresearch.interactive.pi import pi_bridge, pi_context, pi_evidence
from wonder_stockresearch.interactive.pi.pi_attempt import PiAttempt


def execute(
    root: Path,
    session_id: str,
    current_question: str,
    *,
    turn_id: str,
    attempt_id: str,
    model_client: agent_model.AgentModelClient | None,
    skill_context: str = "",
    tool_names: list[str] | tuple[str, ...] | None = None,
    on_progress: progress.ProgressCallback | None = None,
    tool_executor: agent_execution.ToolExecutor = agent_tools.execute_tool,
) -> dict[str, Any]:
    row = agent_session.get_session(root, session_id)
    turn = agent_session.get_turn(root, session_id, turn_id)
    runtime_row = {**row, "turn_id": turn_id}
    question = agent_session.session_question(row)
    tools = agent_tools.tool_catalog(tool_names)
    system = agent_policy.build_system_prompt(
        question.focus, tools, skill_context, native_tools=True
    )
    background = agent_session.initial_context(root, runtime_row, current_question)
    if background.get("previous_interactive_report"):
        previous = background["previous_interactive_report"]
        previous["answer"] = str(previous.get("answer", ""))[:1800]
        previous["claims"] = previous.get("claims", [])[:3]
    with storage.open_db(root) as connection:
        messages = storage.list_agent_messages(connection, turn_id)
        successful = agent_execution.successful_tool_calls(
            storage.list_agent_events(connection, session_id), turn_id
        )
    background["prior_successful_tool_calls"] = [
        {"arguments_hash": key, "result": agent_memory.compact(value, 1600)}
        for key, value in list(successful.items())[-6:]
    ]
    prompt = (
        current_question
        + "\n辅助上下文（观察不是已证实事实）："
        + json.dumps(background, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    history = [item for batch in pi_context.groups(messages) for item in batch]
    budget = agent_execution.AttemptBudget.load(root, row, turn, attempt_id)
    state = agent_execution.ToolExecutionState(
        root=root,
        row=runtime_row,
        turn_id=turn_id,
        attempt_id=attempt_id,
        allowed_names=tuple(item["name"] for item in tools),
        tool_names=tool_names,
        question=current_question,
        successful=successful,
    )
    agent_execution.restore_tool_state(state)
    adapter = PiAttempt(
        root,
        row,
        turn,
        budget,
        state,
        system,
        tools,
        model_client,
        on_progress,
        tool_executor,
        available_refs=pi_evidence.catalog(root, turn_id),
    )
    memory_ids = [
        item["memory_id"] for item in background.get("relevant_memory", []) if item.get("memory_id")
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
    adapter.save_message({"role": "user", "content": current_question, "timestamp": 0})
    envfile.load_env_file(root / ".env")
    try:
        pi_bridge.run(
            {
                "model": turn["model"],
                "system": system,
                "tools": tools,
                "prompt": prompt,
                "history": history,
                "scripted": model_client is not None,
            },
            adapter.handle,
        )
    finally:
        adapter.close()
    adapter.settled({})
    assert adapter.candidate is not None
    return agent_execution.accept_answer(
        root, row, budget, adapter.candidate, current_question, tool_names, on_progress
    )
