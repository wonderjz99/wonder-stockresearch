"""受限的交互式公司研究 Agent。

交互 Agent 与正式 ResearchRun 分离：读取归档并按权限补取只读财务资料，输出临时
InteractiveReport；只有用户明确调用 memo-save/chat save 才会产生公司 Memo。
本模块不执行 collect、source-add、save-draft 或 finish。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from wonder_stockresearch import model_request, observability, progress, skill_registry, storage
from wonder_stockresearch.fixed import research
from wonder_stockresearch.interactive import (
    agent_company,
    agent_memory,
    agent_model,
    agent_policy,
    agent_runtime,
    agent_session,
    agent_tools,
    agent_working_record,
    company_memo,
    question_requirements,
    tool_registry,
)
from wonder_stockresearch.interactive.agent_errors import AgentError

from .models import (
    AgentDecision,
    CompanyMemo,
    EvidenceRef,
    InteractiveFollowup,
    InteractiveQuestion,
    InteractiveReport,
    MemoRequest,
    source_id_text,
)

DEFAULT_PROVIDER = "deepseek"

# 兼容现有 Python 调用者；实际业务操作仅在 agent_session 定义。
_validation_error_details = agent_session.validation_error_details
_session = agent_session.get_session
_turn = agent_session.get_turn
_session_question = agent_session.session_question
_turn_question_text = agent_session.turn_question_text
_initial_context = agent_session.initial_context
_validate_evidence_refs = agent_session.validate_evidence_refs
_append_event = agent_session.append_event
_finish_attempt_and_turn = agent_session.finish_attempt_and_turn
_budget_for_turn = agent_session.budget_for_turn
_start_attempt = agent_session.start_attempt
_record_attempt_progress = agent_session.record_attempt_progress
_decision = agent_session.parse_decision
_decision_trace_attributes = agent_session.decision_trace_attributes
_settle_unfinished_attempt = agent_session.settle_unfinished_attempt


def _pi_enabled() -> bool:
    selected = os.environ.get("WONDER_AGENT_RUNTIME", "legacy")
    if selected not in {"legacy", "pi"}:
        raise AgentError(
            "invalid_agent_runtime", "WONDER_AGENT_RUNTIME 只支持 legacy 或 pi", exit_code=2
        )
    return selected == "pi"


def _check_turn_runtime(turn: dict[str, Any]) -> None:
    _runtime_for_turn(turn)


def _runtime_for_turn(turn: dict[str, Any]) -> str:
    try:
        value = json.loads(turn["question_json"]).get("_agent_runtime", "legacy")
    except (ValueError, TypeError, AttributeError) as error:
        raise AgentError("invalid_turn", "Agent Turn 的运行时记录损坏", exit_code=2) from error
    if not isinstance(value, str) or value not in {"legacy", "pi-v1"}:
        raise AgentError(
            "unsupported_turn_runtime", "此 Turn 使用不兼容的运行时，请重新 ask", exit_code=2
        )
    return value


def _insert_turn(
    connection: Any,
    *,
    turn_id: str,
    session_id: str,
    ticker: str,
    run_id: str | None,
    question_json: str,
    skill_audit: list[dict[str, object]] | None,
    tool_names: list[str] | tuple[str, ...] | None,
    provider: str,
    model: str,
    max_iterations: int,
    token_budget: int,
    created_at: Any,
    root: Path | None = None,
) -> None:
    """固化 QueryWorkspace 所需的用户问题、授权和预算；不承担 19D Attempt 语义。"""
    payload = json.loads(question_json)
    payload["_agent_runtime"] = "pi-v1" if _pi_enabled() else "legacy"
    payload["_quality_version"] = 1
    analysis_time = datetime.fromisoformat(payload["as_of"]) if payload.get("as_of") else created_at
    payload["_analysis_clock"] = question_requirements.freeze_clock(analysis_time)
    if root is not None:
        payload["_question_skill"] = skill_registry.selection_audit(
            question_requirements.load_method(root)
        )
    if payload.get("context") == "company_latest":
        payload["_company_scope"] = agent_company.freeze(connection, ticker, run_id)
    question_json = json.dumps(payload, ensure_ascii=False)
    storage.insert_agent_turn(
        connection,
        turn_id=turn_id,
        session_id=session_id,
        ticker=ticker,
        run_id=run_id,
        question_json=question_json,
        skill_audit_json=json.dumps(skill_audit or [], ensure_ascii=False, sort_keys=True),
        allowed_tools_json=(
            json.dumps(list(tool_names), ensure_ascii=False) if tool_names is not None else None
        ),
        tool_registry_version=tool_registry.REGISTRY_VERSION if tool_names is not None else None,
        provider=provider,
        model=model,
        budget_json=json.dumps(
            {
                "max_iterations": max_iterations,
                "token_budget": token_budget,
                "input_token_limit": payload.get("input_token_limit", 16000),
                "output_token_limit": payload.get("output_token_limit", 4096),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        created_at=created_at,
    )


def _resolve_context_run(
    connection: Any, ticker: str, question: InteractiveQuestion
) -> dict[str, Any] | None:
    if question.run_id:
        run = storage.get_run(connection, question.run_id)
        if run is None:
            raise AgentError("unknown_run", f"找不到 run：{question.run_id}", exit_code=2)
        if run["ticker"] != ticker:
            raise AgentError("run_ticker_mismatch", "run 属于其他股票", exit_code=2)
        return run
    if question.context == "latest_completed_report":
        return storage.latest_run(connection, ticker, completed_only=True) or storage.latest_run(
            connection, ticker
        )
    return storage.latest_run(connection, ticker)


_source_rows = agent_tools.source_rows
_source_summary = agent_tools.source_summary
_memo_summary = agent_tools.memo_summary
_compact = agent_memory.compact


_scratchpad_view = agent_memory.scratchpad_view
_ensure_scratchpad = agent_memory.ensure_scratchpad
_record_tool_observation = agent_memory.record_tool_observation
_complete_scratchpad = agent_memory.complete_scratchpad


_compress_messages = agent_memory.compress_messages
_sync_long_memory = agent_memory.sync_long_memory


_execute_tool = agent_tools.execute_tool
_tool_catalog = agent_tools.tool_catalog


def _system_prompt(
    focus: str,
    skill_context: str = "",
    tool_names: list[str] | tuple[str, ...] | None = None,
) -> str:
    return agent_policy.build_system_prompt(focus, _tool_catalog(tool_names), skill_context)


_memory_summary = agent_memory.memory_summary


def _api_call_for_root(
    root: Path, messages: list[dict[str, str]], model: str
) -> tuple[dict[str, Any], int]:
    return agent_model.deepseek_decision(root, messages, model)


_ORIGINAL_API_CALL = _api_call_for_root


def _execute_session(
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
    on_started: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    with storage.agent_execution_lock(root, session_id):
        try:
            if on_started is not None:
                on_started(session_id, turn_id)
            if _turn(root, session_id, turn_id)["status"] != "running":
                raise AgentError("turn_not_running", "当前 Turn 已停止，不能继续执行", exit_code=2)
            current_turn = _turn(root, session_id, turn_id)
            frozen_question = json.loads(current_turn["question_json"])
            if frozen_question.get("_question_skill"):
                method = question_requirements.load_method(root)
                if skill_registry.selection_audit(method) != frozen_question["_question_skill"]:
                    raise AgentError(
                        "skill_changed_since_selection",
                        "问题澄清 Skill 已改变；请重新发起问题",
                        exit_code=2,
                    )
                if question_requirements.SKILL_NAME not in skill_context:
                    skill_context += "\n" + skill_registry.execution_context(method)
            task = question_requirements.load(root, _session(root, session_id), turn_id)
            if task:
                question_requirements.save(root, _session(root, session_id), turn_id, task)
            if pending_question := question_requirements.question(task):
                from wonder_stockresearch.interactive import agent_execution

                row = _session(root, session_id)
                return agent_execution.accept_answer(
                    root,
                    row,
                    agent_execution.AttemptBudget.load(root, row, current_turn, attempt_id),
                    AgentDecision(type="ask_user", question=pending_question),
                    current_question,
                    tool_names,
                    on_progress,
                )
            if _runtime_for_turn(_turn(root, session_id, turn_id)) == "pi-v1":
                from wonder_stockresearch.interactive.pi import pi_agent

                _check_turn_runtime(_turn(root, session_id, turn_id))
                return pi_agent.execute(
                    root,
                    session_id,
                    current_question,
                    turn_id=turn_id,
                    attempt_id=attempt_id,
                    model_client=model_client
                    or (
                        agent_model.FunctionAgentModelClient(_api_call_for_root)
                        if _api_call_for_root is not _ORIGINAL_API_CALL
                        else None
                    ),
                    skill_context=skill_context,
                    tool_names=tool_names,
                    on_progress=on_progress,
                    tool_executor=_execute_tool,
                )
            return _execute_session_loop(
                root,
                session_id,
                current_question,
                turn_id=turn_id,
                attempt_id=attempt_id,
                model_client=model_client
                or agent_model.FunctionAgentModelClient(_api_call_for_root),
                skill_context=skill_context,
                tool_names=tool_names,
                on_progress=on_progress,
            )
        except KeyboardInterrupt:
            error = AgentError(
                "agent_interrupted", "执行已中断；用 ask-status 查看目标 Turn", exit_code=130
            )
            _settle_unfinished_attempt(root, session_id, turn_id, attempt_id, "interrupted", error)
            raise error from None
        except Exception as cause:  # noqa: BLE001 -- sanitize errors at the execution boundary
            error = (
                cause
                if isinstance(cause, AgentError)
                else AgentError(
                    "agent_execution_failed", "执行失败，已保留进度；用 ask-status 查看目标 Turn"
                )
            )
            _settle_unfinished_attempt(
                root,
                session_id,
                turn_id,
                attempt_id,
                "budget_exhausted" if error.code == "agent_budget_exhausted" else "failed",
                error,
            )
            raise error from None


def _execute_session_loop(
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
) -> dict[str, Any]:
    from wonder_stockresearch.interactive import legacy_agent

    return legacy_agent.execute(
        root,
        session_id,
        current_question,
        turn_id=turn_id,
        attempt_id=attempt_id,
        model_client=model_client,
        skill_context=skill_context,
        tool_names=tool_names,
        on_progress=on_progress,
        tool_executor=_execute_tool,
    )


def _resolved_model(model: str | None) -> str:
    return model_request.resolved_model(model)


def _session_skill_selection_payload(root: Path, session_id: str) -> dict[str, object] | None:
    with storage.open_db(root) as connection:
        events = storage.list_agent_events(connection, session_id)
    for event in reversed(events):
        if event["event_type"] != "skill_selection":
            continue
        try:
            payload = json.loads(str(event["payload_json"]))
        except json.JSONDecodeError:
            continue
        raw_skills = payload.get("skills")
        if isinstance(raw_skills, list) and all(isinstance(item, dict) for item in raw_skills):
            return dict(payload)
    return None


def _session_skill_context(root: Path, session_id: str) -> tuple[str, tuple[str, ...] | None]:
    """续问时恢复相同 Skill 正文和工具授权，拒绝内容或权限静默变化。"""
    selection_payload = _session_skill_selection_payload(root, session_id)
    if selection_payload is None:
        return "", None
    return _skill_context_from_selection(root, selection_payload, scope="session")


def _skill_context_from_selection(
    root: Path, selection_payload: dict[str, object], *, scope: str
) -> tuple[str, tuple[str, ...] | None]:
    """从已冻结的 Skill 审计载入上下文，拒绝权限或内容漂移。"""
    selection = [dict(item) for item in selection_payload["skills"]]  # type: ignore[index]
    names = [str(item.get("name") or "") for item in selection]
    if any(not name for name in names):
        raise AgentError(
            "invalid_skill_selection", f"Agent {scope} 的 Skill 审计记录损坏", exit_code=2
        )
    try:
        loaded = skill_registry.load_selected(root, names)
    except skill_registry.SkillRegistryError as error:
        raise AgentError(error.code, error.message, exit_code=2) from error
    current_audit = skill_registry.selection_audit(loaded)
    normalized_previous = []
    for index, item in enumerate(selection):
        previous = dict(item)
        previous.setdefault("tools", [])
        if index < len(current_audit):
            for field in ("applies_to", "dependencies", "prompt_resources"):
                previous.setdefault(field, current_audit[index][field])
        normalized_previous.append(previous)
    if current_audit != normalized_previous:
        raise AgentError(
            "skill_changed_since_selection",
            "Skill 内容已在会话期间改变；请重新发起临时分析",
            exit_code=2,
        )
    context = skill_registry.execution_context(loaded)
    if len(context) > 30000:
        raise AgentError(
            "skill_context_too_large",
            "选择的 Skill 正文和参考资料超过交互 Agent 上下文上限",
            exit_code=2,
        )

    raw_allowed = selection_payload.get("allowed_tools")
    raw_registry_version = selection_payload.get("tool_registry_version")
    if raw_allowed is None:
        # 历史 session 没有固化 allowlist，继续使用当时的完整内置工具集合。
        # 新注册工具不得加入这组旧工具 ID，避免历史会话静默扩大权限。
        return context, None
    if raw_registry_version != tool_registry.REGISTRY_VERSION:
        raise AgentError(
            "tool_registry_changed", "工具注册表版本已变化；请重新发起临时分析", exit_code=2
        )
    if not isinstance(raw_allowed, list) or any(not isinstance(item, str) for item in raw_allowed):
        raise AgentError("invalid_tool_selection", f"Agent {scope} 的工具授权记录损坏", exit_code=2)
    if not selection:
        # 直接 Python `ask(..., tool_names=...)` 没有可重载的 Skill 正文，但其显式
        # allowlist 已写入事件和 Turn。Registry version 不变时按该冻结集合继续，
        # 否则会把一个临时调用意外降级为 capability 的基础工具集合。
        try:
            return context, tool_registry.granted_tool_ids(raw_allowed, "interactive_research")
        except tool_registry.ToolRegistryError as error:
            raise AgentError(error.code, error.message, exit_code=2) from error
    expected = skill_registry.execution_tool_ids(loaded, "interactive_research")
    if tuple(raw_allowed) != expected:
        raise AgentError(
            "tool_selection_changed", "会话工具授权已变化；请重新发起临时分析", exit_code=2
        )
    return context, expected


def _turn_skill_context(root: Path, turn: dict[str, Any]) -> tuple[str, tuple[str, ...] | None]:
    """恢复目标 Turn 的授权快照；不能借另一个 Turn 的 session 事件扩权。"""
    try:
        audit = json.loads(str(turn["skill_audit_json"]))
        allowed = (
            json.loads(str(turn["allowed_tools_json"])) if turn.get("allowed_tools_json") else None
        )
    except json.JSONDecodeError as error:
        raise AgentError("invalid_turn", "Agent Turn 的 Skill/工具快照损坏", exit_code=1) from error
    if not isinstance(audit, list) or not all(isinstance(item, dict) for item in audit):
        raise AgentError("invalid_turn", "Agent Turn 的 Skill 审计记录损坏", exit_code=1)
    return _skill_context_from_selection(
        root,
        {
            "skills": audit,
            "allowed_tools": allowed,
            "tool_registry_version": turn.get("tool_registry_version"),
        },
        scope="Turn",
    )


def ask(
    root: Path,
    ticker: str,
    question: InteractiveQuestion,
    *,
    model: str | None = None,
    skill_context: str = "",
    skill_audit: list[dict[str, object]] | None = None,
    tool_names: list[str] | tuple[str, ...] | None = None,
    model_client: agent_model.AgentModelClient | None = None,
    on_progress: progress.ProgressCallback | None = None,
    on_started: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    ticker = research._checked_ticker(ticker)
    if len(skill_context) > 30000:
        raise AgentError(
            "skill_context_too_large",
            "选择的 Skill 正文和参考资料超过交互 Agent 上下文上限",
            exit_code=2,
        )
    if tool_names is None and skill_audit is None and question.context == "company_latest":
        tool_names = (*tool_registry.LEGACY_TOOL_IDS, "query_company_data")
    if tool_names is not None:
        try:
            tool_names = tool_registry.granted_tool_ids(list(tool_names), "interactive_research")
        except tool_registry.ToolRegistryError as error:
            raise AgentError(error.code, error.message, exit_code=2) from error
    elif skill_audit is not None:
        requested = [
            str(tool_id)
            for item in skill_audit
            for tool_id in item.get("tools", [])
            if isinstance(tool_id, str)
        ]
        try:
            tool_names = tool_registry.granted_tool_ids(requested, "interactive_research")
        except tool_registry.ToolRegistryError as error:
            raise AgentError(error.code, error.message, exit_code=2) from error
    storage.initialize(root)
    session_id = str(uuid4())
    turn_id = str(uuid4())
    resolved_model = _resolved_model(model)
    created_at = storage.now_utc()
    with (
        storage.project_lock(root),
        storage.open_db(root) as connection,
        storage.transaction(connection),
    ):
        storage.ensure_stock(connection, ticker, created_at)
        run = _resolve_context_run(connection, ticker, question)
        storage.insert_agent_session(
            connection,
            session_id=session_id,
            ticker=ticker,
            run_id=str(run["id"]) if run else None,
            question_json=question.model_dump_json(),
            status="active",
            provider=DEFAULT_PROVIDER,
            model=resolved_model,
            max_iterations=question.max_iterations,
            token_budget=question.token_budget,
            created_at=created_at,
        )
        _insert_turn(
            connection,
            turn_id=turn_id,
            session_id=session_id,
            ticker=ticker,
            run_id=str(run["id"]) if run else None,
            question_json=question.model_dump_json(),
            skill_audit=skill_audit,
            tool_names=tool_names,
            provider=DEFAULT_PROVIDER,
            model=resolved_model,
            max_iterations=question.max_iterations,
            token_budget=question.token_budget,
            created_at=created_at,
            root=root,
        )
    _append_event(
        root,
        session_id,
        "question",
        {"question": question.question},
        turn_id=turn_id,
    )
    _ensure_scratchpad(root, session_id, question.question)
    if skill_audit is not None or tool_names is not None:
        _append_event(
            root,
            session_id,
            "skill_selection",
            {
                "skills": skill_audit or [],
                "allowed_tools": list(tool_names) if tool_names is not None else None,
                "tool_registry_version": tool_registry.REGISTRY_VERSION,
            },
            turn_id=turn_id,
        )
    _, _, attempt_id = _start_attempt(
        root,
        session_id,
        turn_id,
        kind="initial_question",
        input_payload={"question": question.question},
    )
    with observability.operation(
        root,
        "agent.ask",
        trace_kind="agent",
        run_id=str(run["id"]) if run else None,
        session_id=session_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
        attributes={
            "provider": DEFAULT_PROVIDER,
            "model": resolved_model,
            "focus": question.focus,
            "max_iterations": question.max_iterations,
            "token_budget": question.token_budget,
            "question_chars": len(question.question),
            "agent.turn_id": turn_id,
        },
    ):
        with observability.span(
            root,
            "skills.selected",
            span_kind="chain",
            attributes={
                **observability.skill_audit_summary(skill_audit),
                "tool_count": len(tool_names)
                if tool_names is not None
                else len(tool_registry.names()),
                "tool_ids": list(tool_names)
                if tool_names is not None
                else list(tool_registry.names()),
                "tool_registry_version": tool_registry.REGISTRY_VERSION,
            },
        ):
            pass
        return _execute_session(
            root,
            session_id,
            question.question,
            turn_id=turn_id,
            attempt_id=attempt_id,
            model_client=model_client,
            skill_context=skill_context,
            tool_names=tool_names,
            on_progress=on_progress,
            on_started=on_started,
        )


def followup(
    root: Path,
    session_id: str,
    request: InteractiveFollowup,
    *,
    model_client: agent_model.AgentModelClient | None = None,
    on_progress: progress.ProgressCallback | None = None,
    on_started: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    row = _session(root, session_id)
    if row["status"] == "failed":
        raise AgentError("session_failed", "失败的 session 不能继续，请重新 ask", exit_code=2)
    turn_id = str(uuid4())
    with observability.operation(
        root,
        "agent.followup",
        trace_kind="agent",
        run_id=str(row["run_id"]) if row.get("run_id") else None,
        session_id=session_id,
        attributes={
            "provider": str(row["provider"]),
            "model": str(row["model"]),
            "question_chars": len(request.question),
        },
    ):
        with observability.span(
            root,
            "skills.reload",
            span_kind="chain",
            attributes={"session_id": session_id},
        ) as skill_span:
            skill_context, tool_names = _session_skill_context(root, session_id)
            skill_span.set_attribute("skill_context_chars", len(skill_context))
        selection_payload = _session_skill_selection_payload(root, session_id)
        raw_audit = selection_payload.get("skills") if selection_payload else None
        turn_audit = (
            [dict(item) for item in raw_audit]
            if isinstance(raw_audit, list) and all(isinstance(item, dict) for item in raw_audit)
            else None
        )
        created_at = storage.now_utc()
        with storage.project_lock(root), storage.open_db(root) as connection:
            if connection.execute(
                "SELECT 1 FROM agent_attempts WHERE session_id = ? AND status = 'running' LIMIT 1",
                (session_id,),
            ).fetchone():
                raise AgentError(
                    "agent_busy", "会话已有 running Attempt；先查看状态或处理遗留中断", exit_code=2
                )
            _insert_turn(
                connection,
                turn_id=turn_id,
                session_id=session_id,
                ticker=str(row["ticker"]),
                run_id=str(row["run_id"]) if row.get("run_id") else None,
                question_json=json.dumps(
                    {
                        **_session_question(row).model_dump(mode="json"),
                        "kind": "followup",
                        "question": request.question,
                    },
                    ensure_ascii=False,
                ),
                skill_audit=turn_audit,
                tool_names=tool_names,
                provider=str(row["provider"]),
                model=str(row["model"]),
                max_iterations=int(row["max_iterations"]),
                token_budget=int(row["token_budget"]),
                created_at=created_at,
                root=root,
            )
        _append_event(
            root, session_id, "followup", {"turn_id": turn_id, "question": request.question}
        )
        _ensure_scratchpad(root, session_id, request.question)
        _, _, attempt_id = _start_attempt(
            root,
            session_id,
            turn_id,
            kind="followup_question",
            input_payload={"question": request.question},
        )
        observability.link_current_trace(root, turn_id=turn_id, attempt_id=attempt_id)
        return _execute_session(
            root,
            session_id,
            request.question,
            turn_id=turn_id,
            attempt_id=attempt_id,
            model_client=model_client,
            skill_context=skill_context,
            tool_names=tool_names,
            on_progress=on_progress,
            on_started=on_started,
        )


def _run_existing_turn(
    root: Path,
    session_id: str,
    turn: dict[str, Any],
    *,
    current_question: str,
    attempt_kind: str,
    input_payload: dict[str, object],
    operation_name: str,
    model_client: agent_model.AgentModelClient | None,
    on_progress: progress.ProgressCallback | None,
) -> dict[str, Any]:
    """按目标 Turn 的冻结授权与累计预算执行一个新 Attempt。"""
    turn_id = str(turn["id"])
    _check_turn_runtime(turn)
    skill_context, tool_names = _turn_skill_context(root, turn)
    _, _, attempt_id = _start_attempt(
        root, session_id, turn_id, kind=attempt_kind, input_payload=input_payload
    )
    with observability.operation(
        root,
        operation_name,
        trace_kind="agent",
        run_id=str(turn["run_id"]) if turn.get("run_id") else None,
        session_id=session_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
        attributes={
            "provider": str(turn["provider"]),
            "model": str(turn["model"]),
            "agent.turn_id": turn_id,
            "agent.attempt_id": attempt_id,
            "question_chars": len(current_question),
        },
    ):
        return _execute_session(
            root,
            session_id,
            current_question,
            turn_id=turn_id,
            attempt_id=attempt_id,
            model_client=model_client,
            skill_context=skill_context,
            tool_names=tool_names,
            on_progress=on_progress,
        )


def respond(
    root: Path,
    session_id: str,
    turn_id: str,
    request: InteractiveFollowup,
    *,
    model_client: agent_model.AgentModelClient | None = None,
    on_progress: progress.ProgressCallback | None = None,
) -> dict[str, Any]:
    """向等待用户信息的同一 Turn 补充输入。"""
    turn = _turn(root, session_id, turn_id)
    if str(turn["status"]) != "waiting_user":
        raise AgentError(
            "turn_not_waiting_user", "ask-respond 只能作用于 waiting_user 的 Turn", exit_code=2
        )
    _check_turn_runtime(turn)
    question = _turn_input_text(root, turn)
    _append_event(
        root,
        session_id,
        "user_response",
        {"response_chars": len(request.question)},
        turn_id=turn_id,
    )
    return _run_existing_turn(
        root,
        session_id,
        turn,
        current_question=f"{question}\n\n用户补充：{request.question}",
        attempt_kind="user_response",
        input_payload={"response": request.question},
        operation_name="agent.respond",
        model_client=model_client,
        on_progress=on_progress,
    )


def retry(
    root: Path,
    session_id: str,
    turn_id: str,
    *,
    model_client: agent_model.AgentModelClient | None = None,
    on_progress: progress.ProgressCallback | None = None,
) -> dict[str, Any]:
    """重试失败/中断的同一 Turn，预算不会重新开始。"""
    turn = _turn(root, session_id, turn_id)
    if not agent_runtime.retryable_turn_status(str(turn["status"])):
        raise AgentError(
            "turn_not_retryable", "ask-retry 只能作用于 failed 或 interrupted 的 Turn", exit_code=2
        )
    _check_turn_runtime(turn)
    _append_event(root, session_id, "retry_requested", {}, turn_id=turn_id)
    return _run_existing_turn(
        root,
        session_id,
        turn,
        current_question=_turn_input_text(root, turn),
        attempt_kind="retry",
        input_payload={},
        operation_name="agent.retry",
        model_client=model_client,
        on_progress=on_progress,
    )


def _turn_input_text(root: Path, turn: dict[str, Any]) -> str:
    text = _turn_question_text(turn)
    with storage.open_db(root) as connection:
        attempts = storage.list_agent_attempts(connection, str(turn["id"]))
    for item in attempts:
        payload = json.loads(str(item["input_json"]))
        if payload.get("kind") == "user_response" and isinstance(payload.get("response"), str):
            text += f"\n\n用户补充：{payload['response']}"
    return text


def interrupt(root: Path, session_id: str, turn_id: str) -> dict[str, Any]:
    """只恢复没有存活执行进程的 running Turn；不会中断另一个活进程。"""
    _turn(root, session_id, turn_id)
    with storage.agent_execution_lock(root, session_id):
        turn = _turn(root, session_id, turn_id)
        if turn["status"] != "running":
            raise AgentError("turn_not_running", "只有遗留 running Turn 需要标记中断", exit_code=2)
        with storage.open_db(root) as connection:
            attempts = storage.list_agent_attempts(connection, turn_id)
        active = [item for item in attempts if item["status"] == "running"]
        if len(active) != 1:
            raise AgentError(
                "invalid_attempt_state", "当前 Turn 没有唯一 running Attempt", exit_code=2
            )
        _settle_unfinished_attempt(
            root,
            session_id,
            turn_id,
            str(active[0]["id"]),
            "interrupted",
            AgentError("agent_interrupted", "遗留执行已标记中断，未重放外部请求"),
        )
    return {"session_id": session_id, "turn_id": turn_id, "status": "interrupted"}


def get_status(root: Path, session_id: str) -> dict[str, Any]:
    row = _session(root, session_id)
    result: dict[str, Any] = {
        "session_id": row["id"],
        "ticker": row["ticker"],
        "run_id": row["run_id"],
        "status": row["status"],
        "provider": row["provider"],
        "model": row["model"],
        "iteration": row["iteration"],
        "max_iterations": row["max_iterations"],
        "token_budget": row["token_budget"],
        "used_tokens": row["used_tokens"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if row["error_json"]:
        result["error"] = json.loads(row["error_json"])
    with storage.open_db(root) as connection:
        events = storage.list_agent_events(connection, session_id)
        result["event_count"] = len(events)
        turns: list[dict[str, Any]] = []
        for item in storage.list_agent_turns(connection, session_id):
            turns.append(
                {
                    "turn_id": item["id"],
                    "run_id": item["run_id"],
                    "question": _turn_question_text(item),
                    "working_record": agent_working_record.project(
                        agent_working_record.build(
                            events,
                            str(item["id"]),
                            _turn_question_text(item),
                            status=str(item["status"]),
                        )
                    ),
                    "status": item["status"],
                    "iteration": item["iteration"],
                    "used_tokens": item["used_tokens"],
                    "budget": json.loads(str(item["budget_json"])),
                    "allowed_tools": json.loads(str(item["allowed_tools_json"]))
                    if item["allowed_tools_json"]
                    else None,
                    "error": json.loads(str(item["error_json"])) if item["error_json"] else None,
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                    "attempts": [
                        {
                            "attempt_id": attempt["id"],
                            "attempt_index": attempt["attempt_index"],
                            "status": attempt["status"],
                            "iteration": attempt["iteration"],
                            "used_tokens": attempt["used_tokens"],
                            "error": json.loads(str(attempt["error_json"]))
                            if attempt["error_json"]
                            else None,
                            "started_at": attempt["started_at"],
                            "finished_at": attempt["finished_at"],
                        }
                        for attempt in storage.list_agent_attempts(connection, str(item["id"]))
                    ],
                }
            )
        result["turns"] = turns
        reports = storage.list_interactive_reports(connection, session_id)
        result["reports"] = [
            {
                "turn_id": report["turn_id"],
                "attempt_id": report["attempt_id"],
                "created_at": report["created_at"],
                "report": json.loads(str(report["report_json"])),
            }
            for report in reports
        ]
        if reports:
            result["report"] = json.loads(str(reports[-1]["report_json"]))
        scratchpad = _scratchpad_view(storage.get_agent_scratchpad(connection, session_id))
        result["scratchpad"] = scratchpad
        analysis = storage.list_analysis_runs(connection, session_id)
        result["analysis_runs"] = [
            {
                "analysis_id": item["id"],
                "turn_id": item.get("turn_id"),
                "execution_index": item["execution_index"],
                "status": item["status"],
                "dataset_hash": item["dataset_hash"],
                "code_hash": item["code_hash"],
                "sql_hash": item.get("sql_hash"),
                "result_summary": item["result_summary"],
            }
            for item in analysis
        ]
        result["calculation_records"] = scratchpad["calculation_refs"] if scratchpad else []
        result["memory_used"] = [
            event["payload_json"]
            for event in storage.list_agent_events(connection, session_id)
            if event["event_type"] == "memory_selection"
        ]
    return result


def _validate_memo_sources(
    root: Path,
    row: dict[str, Any],
    request: MemoRequest,
    turn_id: str | None = None,
) -> None:
    _validate_memo_references(root, row, request.source_refs, turn_id)


def _validate_memo_references(
    root: Path, row: dict[str, Any], references: list[EvidenceRef], turn_id: str | None
) -> None:
    ticker = str(row["ticker"])
    with storage.open_db(root) as connection:
        for reference in references:
            source = storage.get_source(connection, source_id_text(reference.source_id))
            if source is None or source["ticker"] != ticker:
                raise AgentError(
                    "unknown_source_reference", "Memo 引用了未知或其他公司的来源", exit_code=2
                )
            scoped_row = {**row, "turn_id": turn_id}
            if not agent_company.allowed(connection, scoped_row, source):
                raise AgentError(
                    "unlinked_source_reference", "Memo 来源不属于该 session 的 run", exit_code=2
                )
            if agent_company.scope(connection, scoped_row) is not None:
                agent_company.verify_archive(root, source)
                if not storage.has_agent_evidence_receipt(
                    connection,
                    turn_id=turn_id,
                    source_id=str(reference.source_id),
                    locator=reference.locator,
                    source_sha256=source["sha256"],
                ):
                    raise AgentError(
                        "missing_turn_evidence_receipt",
                        "Memo 引用缺少报告 Turn 的工具回执",
                        exit_code=2,
                    )


def memo_save(root: Path, session_id: str, request: MemoRequest) -> dict[str, Any]:
    row = _session(root, session_id)
    with storage.open_db(root) as connection:
        report = storage.get_interactive_report(connection, session_id)
    if report is None:
        raise AgentError(
            "report_unavailable", "只有已生成临时报告的 session 才能保存 Memo", exit_code=2
        )
    _validate_memo_sources(root, row, request, str(report["turn_id"]))
    memo_id = str(uuid4())
    now = storage.now_utc()
    with (
        storage.project_lock(root),
        storage.open_db(root) as connection,
        storage.transaction(connection),
    ):
        version = storage.next_memo_version(connection, str(row["ticker"]), request.title)
        memo = CompanyMemo(
            memo_id=UUID(memo_id),
            ticker=str(row["ticker"]),
            title=request.title,
            content=request.content,
            source_refs=request.source_refs,
            created_from_session=UUID(session_id),
            created_from_turn=str(report["turn_id"]),
            created_from_report_turn=str(report["turn_id"]),
            tags=request.tags,
            version=version,
            created_at=now,
            updated_at=now,
        )
        storage.insert_company_memo(
            connection,
            memo_id=memo_id,
            ticker=str(row["ticker"]),
            title=request.title,
            content=request.content,
            source_refs_json=json.dumps(
                [ref.model_dump(mode="json") for ref in request.source_refs], ensure_ascii=False
            ),
            created_from_session=session_id,
            created_from_turn=str(report["turn_id"]),
            created_from_report_turn=str(report["turn_id"]),
            tags_json=json.dumps(request.tags, ensure_ascii=False),
            version=version,
            created_at=now,
        )
        storage.upsert_memory_entry(
            connection,
            entry_id=f"memo:{memo_id}",
            ticker=memo.ticker,
            memory_type="memo",
            status="trusted",
            topic=memo.title,
            as_of=None,
            content=memo.content,
            source_refs_json=json.dumps(
                [ref.model_dump(mode="json") for ref in memo.source_refs], ensure_ascii=False
            ),
            origin_ref=f"company_memo:{memo_id}",
            content_hash=hashlib.sha256(memo.content.encode()).hexdigest(),
            created_at=now,
        )
        company_memo.attach(connection, memo.model_dump(mode="json"))
    with storage.project_lock(root):
        path = company_memo.export(root, str(row["ticker"]))
    return {"saved": True, "memo": json.loads(memo.model_dump_json()), "path": path}


def memo_save_report(root: Path, session_id: str, *, turn_id: str | None = None) -> dict[str, Any]:
    """用户 save：追加选定的已完成报告；默认当前 Turn，绝不回退到旧成功回答。"""
    row = _session(root, session_id)
    with storage.agent_execution_lock(root, session_id), storage.project_lock(root):
        with storage.open_db(root) as connection:
            turns = storage.list_agent_turns(connection, session_id)
            turn = (
                storage.get_agent_turn(connection, turn_id)
                if turn_id
                else (turns[-1] if turns else None)
            )
            if turn is None or turn["session_id"] != session_id or turn["ticker"] != row["ticker"]:
                raise AgentError("unknown_turn", "找不到当前会话的目标问题", exit_code=2)
            saved_report = storage.get_interactive_report_for_turn(connection, str(turn["id"]))
            if saved_report is None or turn["status"] not in {"succeeded", "legacy"}:
                raise AgentError(
                    "report_unavailable",
                    "当前问题还没有完成的临时分析，不能保存旧回答代替",
                    exit_code=2,
                )
            try:
                report = InteractiveReport.model_validate_json(saved_report["report_json"])
            except ValidationError as error:
                raise AgentError(
                    "invalid_report", "临时报告记录损坏，未保存备忘录", exit_code=2
                ) from error
            if str(report.session_id) != session_id or report.ticker != row["ticker"]:
                raise AgentError("invalid_report", "临时报告归属不一致", exit_code=2)
            refs = list(
                {
                    (str(ref.source_id), ref.locator, ref.relation): ref
                    for ref in [
                        *report.evidence_refs,
                        *[ref for claim in report.claims for ref in claim.evidence],
                    ]
                }.values()
            )
            _validate_memo_references(root, row, refs, str(turn["id"]))
            existing = connection.execute(
                "SELECT memo_id FROM company_memo_links WHERE report_turn_id=?", (turn["id"],)
            ).fetchone()
            duplicate = existing is not None
            if not duplicate:
                memo_id = str(uuid4())
                now = storage.now_utc()
                content = report.answer
                for title, items in (
                    ("原回答中的结论", [claim.text for claim in report.claims]),
                    ("反方与限制", report.counterarguments),
                    ("未完成事项", report.gaps),
                    ("后续核查", report.suggested_next_checks),
                ):
                    if items:
                        content += f"\n\n### {title}\n\n" + "\n".join(f"- {item}" for item in items)
                content += f"\n\n分析时间：{storage.to_iso(report.created_at)}"
                refs_json = json.dumps(
                    [ref.model_dump(mode="json") for ref in refs], ensure_ascii=False
                )
                with storage.transaction(connection):
                    storage.insert_company_memo(
                        connection,
                        memo_id=memo_id,
                        ticker=report.ticker,
                        title=report.user_question,
                        content=content,
                        source_refs_json=refs_json,
                        created_from_session=session_id,
                        created_from_turn=str(turn["id"]),
                        created_from_report_turn=str(turn["id"]),
                        tags_json='["saved-analysis"]',
                        version=1,
                        created_at=now,
                    )
                    company_memo.attach(
                        connection,
                        {
                            "memo_id": memo_id,
                            "ticker": report.ticker,
                            "created_at": storage.to_iso(now),
                        },
                        report_turn_id=str(turn["id"]),
                    )
                    storage.upsert_memory_entry(
                        connection,
                        entry_id=f"memo:{memo_id}",
                        ticker=report.ticker,
                        memory_type="memo",
                        status="trusted",
                        topic=report.user_question,
                        as_of=None,
                        content=content,
                        source_refs_json=refs_json,
                        origin_ref=f"company_memo:{memo_id}",
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                        created_at=now,
                    )
            notebook = company_memo.list_for_company(connection, report.ticker)[0]
        # 仍持有写锁；文件不会以较旧视图覆盖其他 save 的结果。
        path = company_memo.export(root, report.ticker)
    return {
        "saved": True,
        "already_saved": duplicate,
        "memo": notebook,
        "turn_id": str(turn["id"]),
        "path": path,
    }


def memo_list(root: Path, ticker: str) -> dict[str, Any]:
    ticker = research._checked_ticker(ticker)
    storage.initialize(root)
    with storage.open_db(root) as connection:
        rows = company_memo.list_for_company(connection, ticker)
    return {"ticker": ticker, "memos": rows}


def memo_read(root: Path, memo_id: str, *, version: int | None = None) -> dict[str, Any]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        notebook = company_memo.read(connection, memo_id, version=version)
        if notebook is not None:
            return notebook
        row = storage.get_company_memo(connection, memo_id)
    if version is not None:
        raise AgentError(
            "unknown_memo_version",
            "历史条目 ID 不支持主备忘录版本，请使用 ticker 或主 ID",
            exit_code=2,
        )
    if row is None:
        raise AgentError("unknown_memo", f"找不到 Memo：{memo_id}", exit_code=2)
    return _memo_summary(row, include_content=True)


def memory_search(root: Path, ticker: str, query: str = "") -> dict[str, Any]:
    ticker = research._checked_ticker(ticker)
    storage.initialize(root)
    _sync_long_memory(root, ticker)
    with storage.open_db(root) as connection:
        entries = storage.search_memory_entries(connection, ticker, query, limit=20)
    return {
        "ticker": ticker,
        "query": query,
        "memories": [_memory_summary(item) for item in entries],
    }


def memory_read(root: Path, entry_id: str) -> dict[str, Any]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        entry = storage.get_memory_entry(connection, entry_id)
    if entry is None:
        raise AgentError("unknown_memory", f"找不到记忆：{entry_id}", exit_code=2)
    return _memory_summary(entry, content_limit=None)
