"""独立自然语言入口：路由到固定研究流程或交互 Agent。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from wonder_stockresearch import agent, progress, router, skill_registry, storage, symbols
from wonder_stockresearch.fixed import research, writer

from .models import InteractiveQuestion, RoutePlan, SymbolCandidate, validate_ticker


class ChatError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 1) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


def _row(root: Path, session_id: str) -> dict[str, Any]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        result = storage.get_chat_session(connection, session_id)
    if result is None:
        raise ChatError("unknown_chat_session", f"找不到 chat session：{session_id}", exit_code=2)
    return result


def _history(row: dict[str, Any]) -> list[dict[str, str]]:
    try:
        value = json.loads(str(row["history_json"]))
    except (TypeError, json.JSONDecodeError) as error:
        raise ChatError("invalid_chat_session", "chat session 的历史记录损坏") from error
    return value if isinstance(value, list) else []


def _append_history(history: list[dict[str, str]], role: str, content: str) -> str:
    history.append({"role": role, "content": content})
    return json.dumps(history[-20:], ensure_ascii=False)


def _update(
    root: Path,
    session_id: str,
    *,
    history_json: str,
    status: str | None = None,
    pending: dict[str, Any] | None = None,
    clear_pending: bool = False,
    ticker: str | None = None,
    run_id: str | None = None,
    last_agent_session_id: str | None = None,
) -> None:
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.update_chat_session(
            connection,
            session_id,
            status=status,
            history_json=history_json,
            pending_json=json.dumps(pending, ensure_ascii=False) if pending is not None else None,
            clear_pending=clear_pending,
            ticker=ticker,
            run_id=run_id,
            last_agent_session_id=last_agent_session_id,
            updated_at=storage.now_utc(),
        )


def _new_session(root: Path, text: str, plan: RoutePlan | None = None) -> str:
    storage.initialize(root)
    session_id = str(uuid4())
    history = [{"role": "user", "content": text}]
    pending = {"action": "route", "plan": plan.model_dump(mode="json")} if plan else None
    now = storage.now_utc()
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.insert_chat_session(
            connection,
            session_id=session_id,
            status="active",
            history_json=json.dumps(history, ensure_ascii=False),
            pending_json=json.dumps(pending, ensure_ascii=False) if pending else None,
            ticker=plan.ticker if plan else None,
            run_id=None,
            last_agent_session_id=None,
            created_at=now,
        )
    return session_id


def _pending(row: dict[str, Any]) -> dict[str, Any] | None:
    if not row.get("pending_json"):
        return None
    try:
        value = json.loads(str(row["pending_json"]))
    except json.JSONDecodeError as error:
        raise ChatError("invalid_chat_session", "待确认操作记录损坏") from error
    return value if isinstance(value, dict) else None


def _confirmation(text: str) -> bool | None:
    normalized = text.strip().casefold().replace(" ", "")
    if any(
        word in normalized for word in ("不要", "取消", "否", "不想", "不确认", "不开始", "不定稿")
    ):
        return False
    if normalized in {
        "确认",
        "确认开始",
        "开始",
        "确认定稿",
        "定稿",
        "执行",
        "是",
        "好的",
        "好",
        "yes",
        "y",
        "ok",
        "finish",
        "确认finish",
    }:
        return True
    return None


def _issues_payload(issues: list[research.ValidationIssue]) -> list[dict[str, str]]:
    return [issue.to_dict() for issue in issues]


def _candidate_rows(candidates: list[SymbolCandidate]) -> list[dict[str, Any]]:
    return [candidate.model_dump(mode="json") for candidate in candidates]


def _symbol_message(query: str, candidates: list[SymbolCandidate]) -> str:
    if not candidates:
        return (
            f"暂时没有可靠找到“{query}”对应的港股/美股普通股。"
            "请重新输入完整公司名称或股票代码，例如 HK.06049。"
        )
    unverified = any(not candidate.verified for candidate in candidates)
    suffix = "其中模型建议尚未由行情数据源验证，请务必核对。" if unverified else ""
    if len(candidates) == 1:
        return f"找到一个可能的标的，请输入候选编号、完整公司名称或股票代码。{suffix}"
    return f"“{query}”可能对应多个标的，请输入候选编号、完整公司名称或股票代码。{suffix}"


def _resolve_symbol(
    root: Path,
    session_id: str,
    row: dict[str, Any],
    plan: RoutePlan,
    *,
    query: str,
    model: str | None,
    on_progress: progress.ProgressCallback | None,
) -> dict[str, Any]:
    progress.emit(
        on_progress, "symbol-search", "running", f"正在搜索标的：{query}", session_id=session_id
    )
    result = symbols.resolve(root, query, model=model)
    candidates = result.candidates
    pending = {
        "action": "resolve_symbol",
        "query": query,
        "next_intent": plan.intent,
        "plan": plan.model_dump(mode="json"),
        "candidates": _candidate_rows(candidates),
    }
    history = _history(row)
    history_json = _append_history(
        history, "system", f"标的解析：{query}，候选数 {len(candidates)}"
    )
    _update(
        root,
        session_id,
        history_json=history_json,
        status="awaiting_symbol_selection" if candidates else "waiting_user",
        pending=pending,
    )
    progress.emit(
        on_progress,
        "symbol-search",
        "succeeded" if candidates else "waiting",
        f"找到 {len(candidates)} 个候选标的" if candidates else "没有找到可验证候选",
        session_id=session_id,
    )
    return {
        "session_id": session_id,
        "status": "awaiting_symbol_selection" if candidates else "waiting_user",
        "query": query,
        "candidates": _candidate_rows(candidates),
        "message": _symbol_message(query, candidates),
    }


def _direct_ticker(text: str) -> str | None:
    normalized = text.strip().upper()
    try:
        return validate_ticker(normalized)
    except ValueError:
        return None


def _candidate_selection(text: str, candidates: list[SymbolCandidate]) -> SymbolCandidate | None:
    value = text.strip()
    normalized = symbols.normalize_query(value)
    for candidate in candidates:
        if value == candidate.candidate_id or value.casefold() == candidate.ticker.casefold():
            return candidate
        if normalized and normalized == symbols.normalize_query(candidate.name):
            return candidate
    return None


def _selected_symbol(
    root: Path,
    session_id: str,
    row: dict[str, Any],
    plan: RoutePlan,
    candidate: SymbolCandidate | None,
    *,
    ticker: str | None = None,
    model: str | None,
    on_progress: progress.ProgressCallback | None,
) -> dict[str, Any]:
    selected_ticker = ticker or (candidate.ticker if candidate else None)
    if not selected_ticker:
        raise ChatError("missing_symbol_selection", "没有可用的标的选择", exit_code=2)
    selected_plan = plan.model_copy(
        update={
            "ticker": selected_ticker,
            "company_name": candidate.name if candidate else plan.company_name,
        }
    )
    history = _history(row)
    selection_text = (
        f"已选择：{candidate.name}（{selected_ticker}）"
        if candidate
        else f"已采用用户提供的股票代码：{selected_ticker}"
    )
    history_json = _append_history(history, "system", selection_text)
    if selected_plan.intent == "fixed_research":
        pending = {"action": "start_fixed_research", "plan": selected_plan.model_dump(mode="json")}
        _update(
            root,
            session_id,
            history_json=history_json,
            status="awaiting_confirmation",
            pending=pending,
            ticker=selected_ticker,
        )
        warning = (
            "（该候选来自模型建议，尚未由行情数据源验证。）"
            if candidate and not candidate.verified
            else ""
        )
        return {
            "session_id": session_id,
            "status": "awaiting_confirmation",
            "plan": selected_plan.model_dump(mode="json"),
            "message": f"{selection_text}{warning}确认开始正式公司研究吗？",
        }
    pending = {"action": "start_interactive", "plan": selected_plan.model_dump(mode="json")}
    _update(
        root,
        session_id,
        history_json=history_json,
        status="awaiting_confirmation",
        pending=pending,
        ticker=selected_ticker,
    )
    warning = (
        "（该候选来自模型建议，尚未由行情数据源验证。）"
        if candidate and not candidate.verified
        else ""
    )
    return {
        "session_id": session_id,
        "status": "awaiting_confirmation",
        "plan": selected_plan.model_dump(mode="json"),
        "message": f"{selection_text}{warning}确认开始这次临时分析吗？",
    }


def _load_execution_skills(
    root: Path,
    session_id: str,
    row: dict[str, Any],
    plan: RoutePlan,
    *,
    on_progress: progress.ProgressCallback | None,
) -> tuple[dict[str, Any], tuple[skill_registry.LoadedSkill, ...]]:
    """在执行边界才读 Skill 正文/资源，并把无正文的审计元数据写入 chat history。"""
    capability_name = {
        "fixed_research": "fixed_research",
        "interactive_question": "interactive_research",
    }.get(plan.intent)
    try:
        loaded = (
            skill_registry.load_for_capability(root, plan.skill_names, capability_name)
            if capability_name
            else skill_registry.load_selected(root, plan.skill_names)
        )
    except skill_registry.SkillRegistryError as error:
        raise ChatError(error.code, error.message, exit_code=2) from error
    audit = skill_registry.selection_audit(loaded)
    if not audit:
        return row, ()
    history_json = _append_history(
        _history(row), "system", "Skill 执行加载：" + json.dumps(audit, ensure_ascii=False)
    )
    _update(root, session_id, history_json=history_json)
    updated = dict(row)
    updated["history_json"] = history_json
    progress.emit(
        on_progress,
        "skills",
        "succeeded",
        "已按需加载 Skill：" + "、".join(item["name"] for item in audit),
        session_id=session_id,
    )
    return updated, loaded


def _execute_interactive(
    root: Path,
    session_id: str,
    row: dict[str, Any],
    plan: RoutePlan,
    *,
    model: str | None,
    on_progress: progress.ProgressCallback | None,
) -> dict[str, Any]:
    if not plan.ticker:
        raise ChatError("missing_ticker", "临时分析缺少股票代码", exit_code=2)
    row, loaded = _load_execution_skills(root, session_id, row, plan, on_progress=on_progress)
    tool_names: tuple[str, ...] | None = None
    if loaded:
        try:
            tool_names = skill_registry.execution_tool_ids(loaded, "interactive_research")
        except skill_registry.SkillRegistryError as error:
            raise ChatError(error.code, error.message, exit_code=2) from error
    question = InteractiveQuestion(
        question=plan.question or "请分析这家公司", focus=plan.focus, context="company_latest"
    )

    def link_started(agent_session_id: str, turn_id: str) -> None:
        _update(
            root,
            session_id,
            history_json=str(_row(root, session_id)["history_json"]),
            status="active",
            ticker=plan.ticker,
            last_agent_session_id=agent_session_id,
            pending={
                "action": "resume_interactive",
                "agent_session_id": agent_session_id,
                "turn_id": turn_id,
            },
        )

    try:
        result = agent.ask(
            root,
            plan.ticker,
            question,
            model=model,
            skill_context=skill_registry.execution_context(loaded),
            skill_audit=skill_registry.selection_audit(loaded) if loaded else None,
            tool_names=tool_names,
            on_progress=on_progress,
            on_started=link_started,
        )
    except agent.AgentError as error:
        return _interactive_failure(root, session_id, error)
    return _store_interactive_result(root, session_id, result)


def _store_interactive_result(
    root: Path, session_id: str, result: dict[str, Any]
) -> dict[str, Any]:
    """等待用户与完成分开保存；下一条补充必须恢复同一个 Turn。"""
    waiting = result.get("status") == "waiting_user"
    _update(
        root,
        session_id,
        history_json=_append_history(
            _history(_row(root, session_id)), "assistant", json.dumps(result, ensure_ascii=False)
        ),
        status="waiting_user" if waiting else "completed",
        pending={
            "action": "respond_interactive",
            "agent_session_id": result["session_id"],
            "turn_id": result["turn_id"],
        }
        if waiting
        else None,
        clear_pending=not waiting,
        ticker=result.get("ticker"),
        run_id=str(result.get("run_id")) if result.get("run_id") else None,
        last_agent_session_id=str(result["session_id"]) if result.get("session_id") else None,
    )
    return {
        "session_id": session_id,
        "status": "waiting_user" if waiting else "completed",
        "result": result,
        **(
            {
                "question": result["question"],
                "message": result["question"],
                "agent_session_id": result["session_id"],
                "turn_id": result["turn_id"],
            }
            if waiting
            else {}
        ),
    }


def _interactive_failure(root: Path, session_id: str, error: agent.AgentError) -> dict[str, Any]:
    row = _row(root, session_id)
    pending = _pending(row) or {}
    if pending.get("agent_session_id") and pending.get("turn_id"):
        pending["action"] = "resume_interactive"
    pending["error"] = {"code": error.code, "message": error.message}
    _update(
        root,
        session_id,
        history_json=_append_history(_history(row), "system", error.message),
        status="failed",
        pending=pending,
    )
    return {
        "session_id": session_id,
        "status": "failed",
        "agent_session_id": row.get("last_agent_session_id"),
        "turn_id": pending.get("turn_id"),
        "message": "交互分析暂停，已保留进度。可查看 ask-status；失败或中断的问题可在此输入重试。",
        "issues": [{"code": error.code, "message": error.message, "severity": "error"}],
    }


def _execute_fixed(
    root: Path,
    session_id: str,
    row: dict[str, Any],
    plan: RoutePlan,
    *,
    model: str | None,
    on_progress: progress.ProgressCallback | None,
) -> dict[str, Any]:
    if not plan.ticker:
        return {
            "session_id": session_id,
            "status": "waiting_user",
            "question": "我没有可靠识别出股票代码，请提供股票代码，例如 HK.00941。",
        }
    row, loaded = _load_execution_skills(root, session_id, row, plan, on_progress=on_progress)
    progress.emit(on_progress, "research", "started", f"开始正式研究：{plan.ticker}", run_id=None)
    pending = _pending(row) or {}
    resuming = pending.get("action") == "resume_fixed_research"
    # 兼容旧版本中 Writer 失败后遗留的“启动确认”，避免再次确认创建重复 run。
    if pending.get("action") == "start_fixed_research" and row.get("run_id"):
        with storage.open_db(root) as connection:
            steps = storage.list_steps(connection, str(row["run_id"]))
        pending = {
            **pending,
            "run_id": row["run_id"],
            "stage": "write-draft"
            if any(step["step_key"] == "write_draft" for step in steps)
            else "collect",
        }
        resuming = True
    run_id = (
        str(pending["run_id"]) if resuming else str(research.start_run(root, plan.ticker)["run_id"])
    )
    if loaded and not resuming:
        research.record_skill_selection(root, run_id, skill_registry.selection_audit(loaded))
    history = _history(row)
    history_json = _append_history(history, "system", f"研究 run：{run_id}")
    stage = str(pending.get("stage", "collect")) if resuming else "collect"
    retry = {
        "action": "resume_fixed_research",
        "plan": plan.model_dump(mode="json"),
        "run_id": run_id,
        "stage": stage,
    }
    _update(
        root,
        session_id,
        history_json=history_json,
        ticker=plan.ticker,
        run_id=run_id,
        status="active",
        pending=retry,
    )
    try:
        if stage == "collect":
            collected, _collect_issues = research.collect(
                root, run_id, "all", on_progress=on_progress
            )
            if int(collected.get("succeeded", 0)) == 0:
                raise ChatError(
                    "collection_failed", "没有成功采集到资料，请检查 OpenD 或补充来源后重试"
                )
            stage = "write-draft"
            retry["stage"] = stage
            _update(root, session_id, history_json=history_json, pending=retry)
        _, writer_issues = writer.write_draft(root, run_id, model=model, on_progress=on_progress)
    except (writer.WriterError, research.ResearchError, ChatError, storage.StorageError) as error:
        _update(root, session_id, history_json=history_json, status="failed", pending=retry)
        progress.emit(on_progress, stage, "failed", "研究暂停，可在当前会话确认重试", run_id=run_id)
        return {
            "session_id": session_id,
            "run_id": run_id,
            "ticker": plan.ticker,
            "status": "failed",
            "failed_step": stage,
            "message": "研究暂停，已保留 run 和成功分组。在当前会话输入“重试”可继续。",
            "issues": [
                {
                    "code": getattr(error, "code", "storage_error"),
                    "location": stage,
                    "message": str(error),
                    "severity": "error",
                }
            ],
        }

    progress.emit(on_progress, "validate", "running", "正在校验正式研究草稿", run_id=run_id)
    validation_issues = research.validate_run(root, run_id)
    if not validation_issues and writer_issues:
        validation_issues = writer_issues
    if validation_issues:
        _update(root, session_id, history_json=history_json, status="failed")
        progress.emit(
            on_progress, "validate", "failed", "validate 未通过，不能 finish", run_id=run_id
        )
        return {
            "session_id": session_id,
            "run_id": run_id,
            "ticker": plan.ticker,
            "status": "validation_failed",
            "message": "草稿已生成但未通过证据校验；尚未生成或冻结正式报告。请查看具体问题并修正后再验证。",
            "issues": _issues_payload(validation_issues),
        }

    pending = {"action": "finish", "run_id": run_id, "ticker": plan.ticker}
    history_json = _append_history(history, "system", "validate 通过，等待用户确认 finish")
    _update(
        root,
        session_id,
        history_json=history_json,
        status="awaiting_finish",
        pending=pending,
        ticker=plan.ticker,
        run_id=run_id,
    )
    progress.emit(
        on_progress, "validate", "succeeded", "validate 通过，等待确认 finish", run_id=run_id
    )
    return {
        "session_id": session_id,
        "run_id": run_id,
        "ticker": plan.ticker,
        "status": "awaiting_finish",
        "message": "正式研究草稿已经通过 validate。是否确认 finish 并冻结正式报告？",
    }


def _finish_pending(
    root: Path,
    session_id: str,
    row: dict[str, Any],
    *,
    on_progress: progress.ProgressCallback | None,
) -> dict[str, Any]:
    pending = _pending(row) or {}
    run_id = str(pending.get("run_id") or row.get("run_id") or "")
    if not run_id:
        raise ChatError("missing_pending_run", "待定稿 session 没有 run_id", exit_code=2)
    progress.emit(on_progress, "finish", "running", "正在生成正式归档报告", run_id=run_id)
    try:
        result = research.finish_run(root, run_id)
    except (research.ResearchError, storage.StorageError, OSError) as error:
        issues = getattr(error, "issues", None)
        return {
            "session_id": session_id,
            "run_id": run_id,
            "status": "failed",
            "failed_step": "finish",
            "message": "定稿未完成。请检查具体问题；修正后在当前会话确认重试。",
            "issues": _issues_payload(issues)
            if issues
            else [
                {
                    "code": getattr(error, "code", "artifact_write_failed"),
                    "location": "finish",
                    "message": str(error),
                    "severity": "error",
                }
            ],
        }
    history = _history(row)
    history_json = _append_history(history, "system", "正式报告已 finish 并冻结")
    _update(
        root,
        session_id,
        history_json=history_json,
        status="completed",
        clear_pending=True,
        run_id=run_id,
        ticker=str(row.get("ticker") or pending.get("ticker")),
    )
    progress.emit(on_progress, "finish", "succeeded", "正式报告已归档并冻结", run_id=run_id)
    return {
        "session_id": session_id,
        "status": "completed",
        "run_id": run_id,
        "result": result,
    }


def _continue_symbol_selection(
    root: Path,
    row: dict[str, Any],
    text: str,
    *,
    model: str | None,
    on_progress: progress.ProgressCallback | None,
) -> dict[str, Any]:
    session_id = str(row["id"])
    pending = _pending(row) or {}
    try:
        plan = RoutePlan.model_validate(pending.get("plan") or {})
        candidates = [
            SymbolCandidate.model_validate(item) for item in pending.get("candidates", [])
        ]
    except Exception as error:
        raise ChatError("invalid_symbol_selection", "标的候选记录损坏", exit_code=2) from error

    history_json = _append_history(_history(row), "user", text)
    _update(root, session_id, history_json=history_json)
    row = dict(row)
    row["history_json"] = history_json

    candidate = _candidate_selection(text, candidates)
    if candidate:
        return _selected_symbol(
            root,
            session_id,
            row,
            plan,
            candidate,
            model=model,
            on_progress=on_progress,
        )

    direct_ticker = _direct_ticker(text)
    if direct_ticker and direct_ticker != "SYNTHETIC":
        return _selected_symbol(
            root,
            session_id,
            row,
            plan,
            None,
            ticker=direct_ticker,
            model=model,
            on_progress=on_progress,
        )

    if text.strip().isdigit():
        return {
            "session_id": session_id,
            "status": "awaiting_symbol_selection" if candidates else "waiting_user",
            "query": pending.get("query"),
            "candidates": _candidate_rows(candidates),
            "message": "没有对应的候选编号，请重新输入编号、完整公司名称或股票代码。",
        }

    # 用户可能把简称补全成了“保利物业”；用新输入重新搜索，但仍不自动启动研究。
    return _resolve_symbol(
        root,
        session_id,
        row,
        plan,
        query=text.strip(),
        model=model,
        on_progress=on_progress,
    )


def _continue_existing(
    root: Path,
    row: dict[str, Any],
    text: str,
    *,
    model: str | None,
    on_progress: progress.ProgressCallback | None,
) -> dict[str, Any] | None:
    pending = _pending(row)
    if pending and pending.get("action") == "resolve_symbol":
        if _confirmation(text) is False:
            history_json = _append_history(_history(row), "user", text)
            _update(
                root,
                str(row["id"]),
                history_json=history_json,
                status="cancelled",
                clear_pending=True,
            )
            return {
                "session_id": row["id"],
                "status": "cancelled",
                "message": "已取消标的选择。",
            }
        if _confirmation(text) is True:
            candidates = pending.get("candidates") or []
            return {
                "session_id": row["id"],
                "status": "awaiting_symbol_selection" if candidates else "waiting_user",
                "query": pending.get("query"),
                "candidates": candidates,
                "message": "请先输入候选编号、完整公司名称或股票代码，暂时不能只确认开始。",
            }
        return _continue_symbol_selection(
            root,
            row,
            text,
            model=model,
            on_progress=on_progress,
        )
    history = _history(row)
    if pending and pending.get("action") == "respond_interactive":
        _update(root, str(row["id"]), history_json=_append_history(history, "user", text))
        try:
            result = agent.respond(
                root,
                str(pending["agent_session_id"]),
                str(pending["turn_id"]),
                agent.InteractiveFollowup(question=text),
                on_progress=on_progress,
            )
        except agent.AgentError as error:
            return _interactive_failure(root, str(row["id"]), error)
        return _store_interactive_result(root, str(row["id"]), result)
    decision = _confirmation(text) if pending else None
    if (
        pending
        and pending.get("action") in {"resume_fixed_research", "finish", "resume_interactive"}
        and text.strip() in {"重试", "继续", "retry", "resume"}
    ):
        decision = True
    if pending and decision is False:
        history_json = _append_history(history, "user", text)
        _update(
            root, str(row["id"]), history_json=history_json, status="active", clear_pending=True
        )
        return {
            "session_id": row["id"],
            "status": "cancelled",
            "message": "已取消待执行操作。",
        }
    if pending and decision is True:
        history_json = _append_history(history, "user", text)
        _update(root, str(row["id"]), history_json=history_json)
        if pending.get("action") == "resume_interactive":
            try:
                result = agent.retry(
                    root,
                    str(pending["agent_session_id"]),
                    str(pending["turn_id"]),
                    on_progress=on_progress,
                )
            except agent.AgentError as error:
                return _interactive_failure(root, str(row["id"]), error)
            return _store_interactive_result(root, str(row["id"]), result)
        if pending.get("action") == "finish":
            return _finish_pending(root, str(row["id"]), row, on_progress=on_progress)
        if pending.get("action") in {"start_fixed_research", "resume_fixed_research"}:
            plan = RoutePlan.model_validate(pending["plan"])
            return _execute_fixed(
                root,
                str(row["id"]),
                row,
                plan,
                model=model,
                on_progress=on_progress,
            )
        if pending.get("action") == "start_interactive":
            plan = RoutePlan.model_validate(pending["plan"])
            return _execute_interactive(
                root,
                str(row["id"]),
                row,
                plan,
                model=model,
                on_progress=on_progress,
            )
    if row.get("last_agent_session_id"):
        history_json = _append_history(history, "user", text)
        _update(root, str(row["id"]), history_json=history_json, status="active")

        def link_started(agent_session_id: str, turn_id: str) -> None:
            _update(
                root,
                str(row["id"]),
                history_json=str(_row(root, str(row["id"]))["history_json"]),
                pending={
                    "action": "resume_interactive",
                    "agent_session_id": agent_session_id,
                    "turn_id": turn_id,
                },
            )

        try:
            result = agent.followup(
                root,
                str(row["last_agent_session_id"]),
                agent.InteractiveFollowup(question=text),
                on_progress=on_progress,
                on_started=link_started,
            )
        except agent.AgentError as error:
            return _interactive_failure(root, str(row["id"]), error)
        return _store_interactive_result(root, str(row["id"]), result)
    return None


def run_once(
    root: Path,
    text: str,
    *,
    session_id: str | None = None,
    model: str | None = None,
    on_progress: progress.ProgressCallback | None = None,
) -> dict[str, Any]:
    storage.initialize(root)
    row = _row(root, session_id) if session_id else None
    if text.strip().casefold() in {"save", "/save", "保存"}:
        if row is None or not row.get("last_agent_session_id") or _pending(row):
            return {
                "session_id": session_id,
                "status": "failed",
                "can_continue": row is not None,
                "message": "请先在当前 chat 完成一次临时分析，再输入 save；恢复会话需带 --session-id。",
                "issues": [
                    {
                        "code": "report_unavailable",
                        "severity": "error",
                        "message": "没有可保存的当前临时分析",
                    }
                ],
            }
        try:
            saved = agent.memo_save_report(root, str(row["last_agent_session_id"]))
        except (agent.AgentError, storage.StorageError) as error:
            return {
                "session_id": session_id,
                "status": "failed",
                "can_continue": True,
                "message": error.message,
                "issues": [{"code": error.code, "message": error.message, "severity": "error"}],
            }
        except (sqlite3.Error, OSError):
            return {
                "session_id": session_id,
                "status": "failed",
                "can_continue": True,
                "message": "本地存储操作失败；请检查存储后再次 save，已提交的分析不会重复添加。",
                "issues": [
                    {
                        "code": "memo_storage_failed",
                        "message": "本地存储操作失败",
                        "severity": "error",
                    }
                ],
            }
        memo = saved["memo"]
        message = (
            f"这次分析已在 {memo['ticker']} 公司备忘录中，没有重复添加。"
            if saved["already_saved"]
            else f"已加入 {memo['ticker']} 公司备忘录（版本 {memo['version']}，共 {memo['entry_count']} 条）。"
        )
        history = _history(row)
        _append_history(history, "user", text)
        _update(
            root,
            str(row["id"]),
            history_json=_append_history(
                history,
                "assistant",
                message,
            ),
        )
        return {
            "session_id": row["id"],
            "status": "memo_saved",
            "can_continue": True,
            "message": message + f"\n文件：{root / saved['path']}",
            "result": saved,
        }
    if row:
        continued = _continue_existing(root, row, text, model=model, on_progress=on_progress)
        if continued is not None:
            return continued

    plan = router.route(root, text, model=model, on_progress=on_progress)
    if plan.intent == "fixed_research":
        new_id = _new_session(root, text)
        row = _row(root, new_id)
        if not plan.ticker:
            return _resolve_symbol(
                root,
                new_id,
                row,
                plan,
                query=plan.company_name or text.strip(),
                model=model,
                on_progress=on_progress,
            )
        pending = {"action": "start_fixed_research", "plan": plan.model_dump(mode="json")}
        history = _history(row)
        history_json = _append_history(
            history, "assistant", f"路由为正式研究：{plan.ticker or '待确认公司'}"
        )
        _update(
            root,
            new_id,
            history_json=history_json,
            status="awaiting_confirmation",
            pending=pending,
            ticker=plan.ticker,
        )
        return {
            "session_id": new_id,
            "status": "awaiting_confirmation",
            "plan": plan.model_dump(mode="json"),
            "message": "我理解为正式公司研究。确认后将自动采集资料并生成草稿，是否开始？",
        }

    if plan.intent == "interactive_question":
        if not plan.ticker:
            new_id = _new_session(root, text, plan)
            row = _row(root, new_id)
            return _resolve_symbol(
                root,
                new_id,
                row,
                plan,
                query=plan.company_name or text.strip(),
                model=model,
                on_progress=on_progress,
            )
        new_id = _new_session(root, text)
        row = _row(root, new_id)
        return _execute_interactive(
            root,
            new_id,
            row,
            plan,
            model=model,
            on_progress=on_progress,
        )

    new_id = _new_session(root, text, plan)
    message = (
        "我暂时不能确定你是要启动正式研究还是做临时分析。请明确说‘写正式报告’或‘临时分析’。"
        if plan.intent == "clarify"
        else "这个自然语言入口暂不支持该操作。"
    )
    return {
        "session_id": new_id,
        "status": "waiting_user",
        "message": message,
        "plan": plan.model_dump(mode="json"),
    }
