"""交互会话的领域操作：事务、证据和历史由 Python 统一管理。

CLI 与两种运行时只调用这些普通函数；此模块不依赖 Agent 入口或 Pi。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from wonder_stockresearch import storage
from wonder_stockresearch.interactive import agent_company, agent_memory, agent_runtime, agent_tools
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import (
    AgentDecision,
    EvidenceRef,
    InteractiveQuestion,
    InteractiveReport,
    source_id_text,
)


def validation_error_details(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item.get('loc', ()))}：{item.get('msg', '')}"
        for item in error.errors()[:3]
    )


def get_session(root: Path, session_id: str) -> dict[str, Any]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        row = storage.get_agent_session(connection, session_id)
    if row is None:
        raise AgentError("unknown_session", f"找不到 Agent session：{session_id}", exit_code=2)
    return row


def get_turn(root: Path, session_id: str, turn_id: str) -> dict[str, Any]:
    """读取属于指定 Session 的 Turn，避免命令跨 session 指向历史记录。"""
    storage.initialize(root)
    with storage.open_db(root) as connection:
        row = storage.get_agent_turn(connection, turn_id)
    if row is None or str(row["session_id"]) != session_id:
        raise AgentError("unknown_turn", f"找不到 Agent Turn：{turn_id}", exit_code=2)
    return row


def session_question(row: dict[str, Any]) -> InteractiveQuestion:
    try:
        return InteractiveQuestion.model_validate_json(row["question_json"])
    except (TypeError, ValidationError) as error:
        raise AgentError("invalid_session", "Agent session 的问题记录损坏", exit_code=1) from error


def turn_question_text(row: dict[str, Any]) -> str:
    """Turn 的不可变提问快照既兼容初始问题，也兼容 follow-up 的简化 JSON。"""
    try:
        payload = json.loads(str(row["question_json"]))
    except (TypeError, json.JSONDecodeError) as error:
        raise AgentError("invalid_turn", "Agent Turn 的问题记录损坏", exit_code=1) from error
    question = payload.get("question") if isinstance(payload, dict) else None
    if not isinstance(question, str) or not question.strip():
        raise AgentError("invalid_turn", "Agent Turn 缺少问题文本", exit_code=1)
    return question


def initial_context(root: Path, row: dict[str, Any], current_question: str) -> dict[str, Any]:
    from wonder_stockresearch.interactive import question_requirements

    ticker = str(row["ticker"])
    run_id = str(row["run_id"]) if row.get("run_id") else None
    agent_memory.sync_long_memory(root, ticker)
    with storage.open_db(root) as connection:
        run = storage.get_run(connection, run_id) if run_id else None
        scoped_sources = agent_company.context_sources(connection, row)
        if agent_company.scope(connection, row) is not None:
            # 公司数据入口优先展示四类查询资料；其余来源仍可通过 list_sources 发现。
            scoped_sources = sorted(
                scoped_sources,
                key=lambda item: (
                    json.loads(item["metadata_json"]).get("dataset") not in agent_company.DATASETS,
                    item["id"],
                ),
            )[:4]
        sources = [
            {
                **{
                    key: value
                    for key, value in agent_tools.source_summary(item).items()
                    if key != "metadata"
                },
                "dataset": json.loads(item["metadata_json"]).get("dataset"),
            }
            for item in scoped_sources
        ]
        company_scope = agent_company.scope(connection, row)
        memory_rows = storage.search_memory_entries(
            connection, ticker, current_question, limit=6 if company_scope is not None else 2
        )
        if company_scope is not None:
            memory_rows = [item for item in memory_rows if item["status"] == "trusted"][:2]
        memories = [agent_memory.memory_summary(item, content_limit=1200) for item in memory_rows]
        for memory in memories:
            refs = memory["source_refs"]
            memory["source_refs"] = refs[:3]
            memory["source_refs_omitted"] = max(0, len(refs) - 3)
        scratchpad = agent_memory.scratchpad_view(
            storage.get_agent_scratchpad(connection, str(row["id"]))
        )
    context: dict[str, Any] = {
        "ticker": ticker,
        "run_id": run_id,
        "run_status": run.get("status") if run else None,
        "sources": sources[:16],
        "relevant_memory": memories,
        "scratchpad": scratchpad,
        "formal_report_available": bool(run and run["status"] == "completed"),
        "data_scope": session_question(row).context,
    }
    if row.get("turn_id"):
        task = question_requirements.load(root, row, str(row["turn_id"]))
        context["question_requirements"] = task
        context["analysis_clock"] = task.get("analysis_clock", {})
    with storage.open_db(root) as connection:
        latest_report = storage.get_interactive_report(connection, str(row["id"]))
    if latest_report is not None:
        previous = json.loads(str(latest_report["report_json"]))
        context["previous_interactive_report"] = {
            "answer": previous.get("answer"),
            "claims": previous.get("claims", []),
            "gaps": previous.get("gaps", []),
            "suggested_next_checks": previous.get("suggested_next_checks", []),
        }
    return context


def validate_evidence_refs(
    root: Path,
    row: dict[str, Any],
    refs: list[EvidenceRef],
    *,
    turn_id: str | None = None,
    require_turn_receipts: bool = False,
) -> None:
    ticker = str(row["ticker"])
    with storage.open_db(root) as connection:
        for ref in refs:
            source = storage.get_source(connection, source_id_text(ref.source_id))
            if source is None or source["ticker"] != ticker:
                raise AgentError(
                    "unknown_source_reference",
                    f"Agent 引用了不属于当前公司的 source_id：{ref.source_id}",
                    exit_code=2,
                )
            scoped_row = {**row, "turn_id": turn_id}
            company_scope = agent_company.scope(connection, scoped_row)
            if not agent_company.allowed(connection, scoped_row, source):
                raise AgentError(
                    "unlinked_source_reference",
                    f"Agent 引用了不属于当前 run 的 source_id：{ref.source_id}",
                    exit_code=2,
                )
            if company_scope is not None:
                agent_company.verify_archive(root, source)
            if (require_turn_receipts or company_scope is not None) and (
                turn_id is None
                or not storage.has_agent_evidence_receipt(
                    connection,
                    turn_id=turn_id,
                    source_id=source_id_text(ref.source_id),
                    locator=ref.locator,
                    source_sha256=str(source["sha256"]),
                )
            ):
                raise AgentError(
                    "missing_turn_evidence_receipt",
                    f"引用未匹配当前 Turn 回执：source_id={ref.source_id}，locator={ref.locator[:500]}；请使用实际返回的完整定位",
                    exit_code=2,
                )


def append_event(
    root: Path,
    session_id: str,
    event_type: str,
    payload: object,
    *,
    turn_id: str | None = None,
    attempt_id: str | None = None,
) -> None:
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.append_agent_event(
            connection,
            session_id,
            event_type,
            agent_memory.compact(payload),
            storage.now_utc(),
            turn_id=turn_id,
            attempt_id=attempt_id,
        )


def finish_attempt_and_turn(
    root: Path,
    session_id: str,
    *,
    turn_id: str,
    attempt_id: str,
    status: str,
    turn_iteration: int,
    turn_used_tokens: int,
    attempt_iteration: int,
    attempt_used_tokens: int,
    session_iteration: int,
    session_used_tokens: int,
    result: InteractiveReport | None = None,
    error: AgentError | None = None,
) -> None:
    """原子完成一个 Attempt 和其目标 Turn，同时保留旧报告。

    Session 仅是会话级汇总/兼容视图；可恢复的事实状态、预算和报告都绑定在
    Turn。因而 follow-up 或 retry 绝不会覆写此前 Turn 的产物。
    """
    with (
        storage.project_lock(root),
        storage.open_db(root) as connection,
        storage.transaction(connection),
    ):
        turn = storage.get_agent_turn(connection, turn_id)
        if turn is None or str(turn["session_id"]) != session_id:
            raise AgentError("unknown_turn", f"找不到 Agent Turn：{turn_id}", exit_code=2)
        try:
            agent_runtime.check_turn_transition(str(turn["status"]), status)
        except agent_runtime.RuntimeTransitionError as transition_error:
            raise AgentError(
                "invalid_turn_transition", str(transition_error), exit_code=2
            ) from transition_error
        error_json = (
            json.dumps({"code": error.code, "message": error.message}, ensure_ascii=False)
            if error
            else None
        )
        if not storage.finish_agent_attempt(
            connection,
            attempt_id,
            status=status,
            iteration=attempt_iteration,
            used_tokens=attempt_used_tokens,
            error_json=error_json,
            finished_at=storage.now_utc(),
        ):
            raise AgentError("unknown_attempt", f"找不到 Agent Attempt：{attempt_id}", exit_code=2)
        storage.update_agent_turn(
            connection,
            turn_id,
            status=status,
            iteration=turn_iteration,
            used_tokens=turn_used_tokens,
            error_json=error_json,
            clear_error=error is None,
            updated_at=storage.now_utc(),
        )
        session_status = {
            "succeeded": "completed",
            "waiting_user": "waiting_user",
            "failed": "failed",
            "budget_exhausted": "budget_exhausted",
            "interrupted": "failed",
        }[status]
        storage.update_agent_session(
            connection,
            session_id,
            status=session_status,
            iteration=session_iteration,
            used_tokens=session_used_tokens,
            error_json=error_json,
            updated_at=storage.now_utc(),
        )
        if result:
            storage.insert_interactive_report(
                connection,
                turn_id=turn_id,
                session_id=session_id,
                attempt_id=attempt_id,
                ticker=result.ticker,
                report_json=result.model_dump_json(),
                created_at=result.created_at,
            )
            episode = {
                "answer": result.answer,
                "claims": [item.model_dump(mode="json") for item in result.claims],
                "gaps": result.gaps,
                "suggested_next_checks": result.suggested_next_checks,
            }
            storage.upsert_memory_entry(
                connection,
                entry_id=f"episode:{turn_id}",
                ticker=result.ticker,
                memory_type="episode",
                status="provisional",
                topic=result.user_question[:500],
                as_of=None,
                content=json.dumps(episode, ensure_ascii=False),
                source_refs_json=json.dumps(
                    [item.model_dump(mode="json") for item in result.evidence_refs],
                    ensure_ascii=False,
                ),
                origin_ref=f"interactive_report:{turn_id}",
                content_hash=agent_memory.hash_json(episode),
                created_at=result.created_at,
            )


def budget_for_turn(turn: dict[str, Any]) -> tuple[int, int]:
    try:
        budget = json.loads(str(turn["budget_json"]))
        return int(budget["max_iterations"]), int(budget["token_budget"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise AgentError("invalid_turn", "Agent Turn 的预算记录损坏", exit_code=1) from error


def start_attempt(
    root: Path,
    session_id: str,
    turn_id: str,
    *,
    kind: str,
    input_payload: dict[str, object],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """为一个已有 Turn 创建新 Attempt；预算从 Turn 累计值继续计算。"""
    now = storage.now_utc()
    with storage.project_lock(root), storage.open_db(root) as connection:
        session = storage.get_agent_session(connection, session_id)
        turn = storage.get_agent_turn(connection, turn_id)
        if session is None:
            raise AgentError("unknown_session", f"找不到 Agent session：{session_id}", exit_code=2)
        if turn is None or str(turn["session_id"]) != session_id:
            raise AgentError("unknown_turn", f"找不到 Agent Turn：{turn_id}", exit_code=2)
        if connection.execute(
            "SELECT 1 FROM agent_attempts WHERE session_id = ? AND status = 'running' LIMIT 1",
            (session_id,),
        ).fetchone():
            raise AgentError(
                "agent_busy", "会话已有 running Attempt；先查看状态或处理遗留中断", exit_code=2
            )
        max_iterations, token_budget = budget_for_turn(turn)
        if int(turn["iteration"]) >= max_iterations or int(turn["used_tokens"]) >= token_budget:
            # 这是开始新 Attempt 前已确认的终态，不能通过 retry 重新获得预算。
            current = str(turn["status"])
            if current != "budget_exhausted":
                try:
                    agent_runtime.check_turn_transition(current, "budget_exhausted")
                except agent_runtime.RuntimeTransitionError as transition_error:
                    raise AgentError(
                        "turn_not_resumable", str(transition_error), exit_code=2
                    ) from transition_error
                with storage.transaction(connection):
                    storage.update_agent_turn(
                        connection,
                        turn_id,
                        status="budget_exhausted",
                        iteration=int(turn["iteration"]),
                        used_tokens=int(turn["used_tokens"]),
                        error_json=json.dumps(
                            {"code": "agent_budget_exhausted"}, ensure_ascii=False
                        ),
                        updated_at=now,
                    )
                    storage.update_agent_session(
                        connection,
                        session_id,
                        status="budget_exhausted",
                        updated_at=now,
                    )
            raise AgentError(
                "agent_budget_exhausted", "该 Turn 已达到既定预算，不能重置", exit_code=1
            )
        with storage.transaction(connection):
            try:
                agent_runtime.check_turn_transition(str(turn["status"]), "running")
            except agent_runtime.RuntimeTransitionError as transition_error:
                raise AgentError(
                    "turn_not_resumable", str(transition_error), exit_code=2
                ) from transition_error
            attempt_id = str(uuid4())
            storage.insert_agent_attempt(
                connection,
                attempt_id=attempt_id,
                session_id=session_id,
                turn_id=turn_id,
                attempt_index=storage.next_agent_attempt_index(connection, turn_id),
                status="running",
                input_json=json.dumps({"kind": kind, **input_payload}, ensure_ascii=False),
                started_at=now,
            )
            storage.update_agent_turn(
                connection,
                turn_id,
                status="running",
                iteration=int(turn["iteration"]),
                used_tokens=int(turn["used_tokens"]),
                clear_error=True,
                updated_at=now,
            )
            storage.update_agent_session(connection, session_id, status="active", updated_at=now)
            storage.append_agent_event(
                connection,
                session_id,
                "attempt_started",
                json.dumps({"kind": kind}, ensure_ascii=False),
                now,
                turn_id=turn_id,
                attempt_id=attempt_id,
            )
    return session, turn, attempt_id


def record_attempt_progress(
    root: Path,
    session_id: str,
    turn_id: str,
    *,
    attempt_id: str,
    attempt_iteration: int,
    attempt_used_tokens: int,
    turn_iteration: int,
    turn_used_tokens: int,
    session_iteration: int,
    session_used_tokens: int,
) -> None:
    with (
        storage.project_lock(root),
        storage.open_db(root) as connection,
        storage.transaction(connection),
    ):
        connection.execute(
            "UPDATE agent_attempts SET iteration = ?, used_tokens = ? WHERE id = ? AND status = 'running'",
            (attempt_iteration, attempt_used_tokens, attempt_id),
        )
        storage.update_agent_turn(
            connection,
            turn_id,
            status="running",
            iteration=turn_iteration,
            used_tokens=turn_used_tokens,
            updated_at=storage.now_utc(),
        )
        storage.update_agent_session(
            connection,
            session_id,
            status="active",
            iteration=session_iteration,
            used_tokens=session_used_tokens,
            updated_at=storage.now_utc(),
        )


def parse_decision(payload: object) -> AgentDecision:
    try:
        return AgentDecision.model_validate(payload)
    except ValidationError as error:
        raise AgentError(
            "invalid_decision",
            f"Agent 决策不符合协议：{validation_error_details(error)}",
            exit_code=1,
        ) from error


def decision_trace_attributes(
    decision: AgentDecision, *, iteration: int, consumed_tokens: int
) -> dict[str, object]:
    """记录 Agent 决策形状，不记录模型正文或工具参数值。"""
    return {
        "iteration": iteration,
        "decision_type": decision.type,
        "tool": decision.tool or "",
        "argument_count": len(decision.arguments),
        "argument_keys": sorted(str(key) for key in decision.arguments),
        "consumed_tokens": consumed_tokens,
        "claim_count": len(decision.claims),
        "evidence_ref_count": len(decision.evidence_refs),
    }


def settle_unfinished_attempt(
    root: Path, session_id: str, turn_id: str, attempt_id: str, status: str, error: AgentError
) -> None:
    """使用已持久化进度结束异常执行，绝不改写已提交的成功报告。"""
    with storage.open_db(root) as connection:
        session = storage.get_agent_session(connection, session_id)
        turn = storage.get_agent_turn(connection, turn_id)
        attempt = storage.get_agent_attempt(connection, attempt_id)
    if not session or not turn or not attempt or attempt["status"] != "running":
        return
    finish_attempt_and_turn(
        root,
        session_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
        status=status,
        turn_iteration=int(turn["iteration"]),
        turn_used_tokens=int(turn["used_tokens"]),
        attempt_iteration=int(attempt["iteration"]),
        attempt_used_tokens=int(attempt["used_tokens"]),
        session_iteration=int(session["iteration"]),
        session_used_tokens=int(session["used_tokens"]),
        error=error,
    )
