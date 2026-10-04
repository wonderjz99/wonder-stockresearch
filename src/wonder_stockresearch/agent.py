"""受限的交互式公司研究 Agent。

交互 Agent 与正式 ResearchRun 分离：它只读取现有资料，输出临时
InteractiveReport；只有用户明确调用 memo-save 才会产生公司 Memo。
本模块不执行 collect、source-add、save-draft 或 finish。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from . import analysis_workspace, envfile, progress, research, skill_registry, storage, writer
from .models import (
    AgentDecision,
    CompanyMemo,
    EvidenceRef,
    InteractiveFollowup,
    InteractiveQuestion,
    InteractiveReport,
    MemoRequest,
    StrictModel,
    source_id_text,
)

DEFAULT_PROVIDER = "deepseek"
DEFAULT_MAX_RESULT_CHARS = 12000
MAX_CONTEXT_CHARS = 40000
MAX_SEARCH_RESULTS = 5


class AgentError(Exception):
    """交互 Agent 错误；不暴露密钥、认证头或完整提示词。"""

    def __init__(self, code: str, message: str, *, exit_code: int = 1) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


class EmptyArgs(StrictModel):
    pass


class SourceReadArgs(StrictModel):
    source_id: UUID
    page: int | None = None
    start_line: int | None = None
    limit_lines: int | None = None


class SearchArgs(StrictModel):
    query: str
    max_results: int = 5


class MemoReadArgs(StrictModel):
    memo_id: UUID


class FinancialQueryArgs(StrictModel):
    query: str = ""
    max_records: int = 80


class TrustedCalculationArgs(StrictModel):
    metric: str


class AnalysisDatasetArgs(StrictModel):
    query: str = ""
    max_records: int = 120


class AnalysisCodeArgs(StrictModel):
    analysis_id: UUID
    code: str


class AnalysisResultArgs(StrictModel):
    analysis_id: UUID


TOOL_ARGS: dict[str, type[BaseModel]] = {
    "get_run_status": EmptyArgs,
    "list_sources": EmptyArgs,
    "read_source": SourceReadArgs,
    "search_archived_sources": SearchArgs,
    "read_report": EmptyArgs,
    "list_memos": EmptyArgs,
    "read_memo": MemoReadArgs,
    "query_archived_financials": FinancialQueryArgs,
    "run_trusted_calculation": TrustedCalculationArgs,
    "create_analysis_dataset": AnalysisDatasetArgs,
    "run_analysis_code": AnalysisCodeArgs,
    "read_analysis_result": AnalysisResultArgs,
}

TOOL_DESCRIPTIONS = {
    "get_run_status": "读取当前研究 run 的状态、来源数量和正式报告状态。",
    "list_sources": "列出当前公司或 run 已归档来源的元数据和 source_id。",
    "read_source": "分段读取已归档来源；引用时只能使用真实返回的 locator。",
    "search_archived_sources": "在当前 run 的已归档文本中做受限关键词搜索。",
    "read_report": "读取当前 run 已完成的正式 company.md，不能修改它。",
    "list_memos": "列出该公司的长期 Memo。",
    "read_memo": "读取一条已经由用户明确保存的公司 Memo。",
    "query_archived_financials": "检索当前公司已归档的富途/官方结构化财务数值和真实来源定位。",
    "run_trusted_calculation": "运行 Python 固定计算：net_profit_yoy_growth、roe_series、capex_to_revenue 或 cash_capex_proxy。",
    "create_analysis_dataset": "从已归档财务数值导出最小只读 JSON 数据集，供一次受限临时计算使用。",
    "run_analysis_code": "只在已创建的数据集上执行受限 Python AST 子集；不能访问网络、环境变量、SQLite 或项目文件。",
    "read_analysis_result": "读取某次已审计临时计算的结果和来源引用。",
}

FOCUS_INSTRUCTIONS = {
    "general": "围绕用户问题回答，不自动扩大为标准公司报告。",
    "challenge": "优先寻找最强反方、支持反方的证据、当前结论的脆弱假设和可能推翻结论的条件。",
    "macro": "区分宏观事实、传导机制和公司特有判断；没有宏观数据时明确缺口，不用公司数据冒充宏观数据。",
    "peer": "比较同行时逐项说明公司、期间、币种、单位和来源；不要把临时比较变成股票池筛选或投资建议。",
    "capacity_cycle": "关注产能、利用率、资本开支、供需和价格传导；区分公司披露与行业推断。",
    "inventory_cycle": "关注库存、库存周转、订单、需求和周期阶段；缺乏库存数据时明确标记无法判断。",
}

T = TypeVar("T", bound=BaseModel)


def _json_error_details(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item.get('loc', ())) }：{item.get('msg', '')}"
        for item in error.errors()[:3]
    )


def _validate_model(model: type[T], payload: object, code: str) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise AgentError(code, f"输入不符合要求：{_json_error_details(error)}", exit_code=2) from error


def _session(root: Path, session_id: str) -> dict[str, Any]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        row = storage.get_agent_session(connection, session_id)
    if row is None:
        raise AgentError("unknown_session", f"找不到 Agent session：{session_id}", exit_code=2)
    return row


def _session_question(row: dict[str, Any]) -> InteractiveQuestion:
    try:
        return InteractiveQuestion.model_validate_json(row["question_json"])
    except (TypeError, ValidationError) as error:
        raise AgentError("invalid_session", "Agent session 的问题记录损坏", exit_code=1) from error


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


def _source_rows(connection: Any, ticker: str, run_id: str | None) -> list[dict[str, Any]]:
    if run_id:
        return storage.get_run_sources(connection, run_id)
    return storage.list_sources_for_ticker(connection, ticker)


def _source_summary(row: dict[str, Any]) -> dict[str, Any]:
    source = json.loads(row["source_json"])
    return {
        "source_id": row["id"],
        "kind": row["kind"],
        "title": source.get("title"),
        "published_at": source.get("published_at"),
        "retrieved_at": row["retrieved_at"],
        "extraction_status": source.get("extraction_status"),
        "metadata": source.get("metadata", {}),
    }


def _memo_summary(row: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "memo_id": row["id"],
        "ticker": row["ticker"],
        "title": row["title"],
        "version": row["version"],
        "tags": json.loads(row["tags_json"]),
        "source_refs": json.loads(row["source_refs_json"]),
        "created_from_session": row["created_from_session"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_content:
        data["content"] = row["content"]
    return data


def _compact(value: object, limit: int = DEFAULT_MAX_RESULT_CHARS) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + "…（结果已截断）"


def _hash_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scratchpad_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "revision": row["revision"],
        "objective": row["objective"],
        "plan": json.loads(row["plan_json"]),
        "observations": json.loads(row["observations_json"]),
        "calculation_refs": json.loads(row["calculation_refs_json"]),
        "source_refs": json.loads(row["source_refs_json"]),
        "open_questions": json.loads(row["open_questions_json"]),
        "compression_count": row["compression_count"],
    }


def _save_scratchpad(
    root: Path,
    session_id: str,
    *,
    objective: str,
    plan: list[str],
    observations: list[dict[str, Any]],
    calculation_refs: list[dict[str, Any]],
    source_refs: list[dict[str, Any]],
    open_questions: list[str],
    compression_count: int,
) -> dict[str, Any]:
    body = {
        "objective": objective,
        "plan": plan[:6],
        "observations": observations[-12:],
        "calculation_refs": calculation_refs[-12:],
        "source_refs": source_refs[-24:],
        "open_questions": open_questions[-8:],
        "compression_count": compression_count,
    }
    with storage.project_lock(root), storage.open_db(root) as connection:
        previous = storage.get_agent_scratchpad(connection, session_id)
        revision = int(previous["revision"]) + 1 if previous else 1
        storage.upsert_agent_scratchpad(
            connection,
            session_id=session_id,
            revision=revision,
            objective=objective[:12000],
            plan_json=json.dumps(body["plan"], ensure_ascii=False),
            observations_json=json.dumps(body["observations"], ensure_ascii=False),
            calculation_refs_json=json.dumps(body["calculation_refs"], ensure_ascii=False),
            source_refs_json=json.dumps(body["source_refs"], ensure_ascii=False),
            open_questions_json=json.dumps(body["open_questions"], ensure_ascii=False),
            content_hash=_hash_json(body),
            compression_count=compression_count,
            updated_at=storage.now_utc(),
        )
    return {"revision": revision, **body}


def _ensure_scratchpad(root: Path, session_id: str, objective: str) -> dict[str, Any]:
    with storage.open_db(root) as connection:
        previous = storage.get_agent_scratchpad(connection, session_id)
    view = _scratchpad_view(previous)
    if view is not None:
        return view
    return _save_scratchpad(
        root,
        session_id,
        objective=objective,
        plan=["读取已归档资料", "优先使用可信固定计算", "核对期间、口径和来源后回答"],
        observations=[],
        calculation_refs=[],
        source_refs=[],
        open_questions=[objective],
        compression_count=0,
    )


def _refs_in(value: object) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        source_id = value.get("source_id")
        locator = value.get("locator")
        if source_id and locator:
            found.append({"source_id": str(source_id), "locator": str(locator)})
        for child in value.values():
            found.extend(_refs_in(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_refs_in(child))
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in found:
        unique[(item["source_id"], item["locator"])] = item
    return list(unique.values())


def _record_tool_observation(
    root: Path, session_id: str, objective: str, tool: str, result: dict[str, Any]
) -> dict[str, Any]:
    current = _ensure_scratchpad(root, session_id, objective)
    observations = list(current["observations"])
    observations.append({"tool": tool, "summary": _compact(result, 1400)})
    source_refs = [*current["source_refs"], *_refs_in(result)]
    unique_refs = {(item["source_id"], item["locator"]): item for item in source_refs if "source_id" in item and "locator" in item}
    calculation_refs = list(current["calculation_refs"])
    if tool == "run_trusted_calculation" and isinstance(result.get("calculation"), dict):
        calculation = result["calculation"]
        calculation_refs.append({"metric": calculation.get("metric"), "version": calculation.get("version")})
    if tool in {"run_analysis_code", "read_analysis_result"}:
        calculation_refs.append({"analysis_id": result.get("analysis_id"), "status": result.get("status")})
    return _save_scratchpad(
        root,
        session_id,
        objective=objective,
        plan=list(current["plan"]),
        observations=observations,
        calculation_refs=calculation_refs,
        source_refs=list(unique_refs.values()),
        open_questions=list(current["open_questions"]),
        compression_count=int(current["compression_count"]),
    )


def _complete_scratchpad(root: Path, session_id: str, objective: str) -> None:
    current = _ensure_scratchpad(root, session_id, objective)
    _save_scratchpad(
        root,
        session_id,
        objective=objective,
        plan=list(current["plan"]),
        observations=[*current["observations"], {"event": "interactive_report_completed"}],
        calculation_refs=list(current["calculation_refs"]),
        source_refs=list(current["source_refs"]),
        open_questions=[],
        compression_count=int(current["compression_count"]),
    )


def _compress_messages(
    root: Path, session_id: str, objective: str, messages: list[dict[str, str]]
) -> list[dict[str, str]]:
    """在上下文预算的 70% 处使用结构化工作记忆替换旧工具文本，完整轨迹仍在 SQLite。"""
    if sum(len(message["content"]) for message in messages) < int(MAX_CONTEXT_CHARS * 0.7):
        return messages
    current = _ensure_scratchpad(root, session_id, objective)
    compressed = _save_scratchpad(
        root,
        session_id,
        objective=objective,
        plan=list(current["plan"]),
        observations=list(current["observations"]),
        calculation_refs=list(current["calculation_refs"]),
        source_refs=list(current["source_refs"]),
        open_questions=list(current["open_questions"]),
        compression_count=int(current["compression_count"]) + 1,
    )
    return [
        messages[0],
        {
            "role": "user",
            "content": (
                f"原问题：{objective}\n"
                f"这是已验证的结构化工作记忆（第 {compressed['compression_count']} 次压缩）："
                f"{json.dumps(compressed, ensure_ascii=False)}\n"
                "继续调用受控工具或给出 final；不要把 provisional episode 当作原始证据。"
            ),
        },
    ]


def _sync_long_memory(root: Path, ticker: str) -> None:
    """只从已完成报告和用户明确保存的 Memo 确定性写入可信长期记忆。"""
    with storage.project_lock(root), storage.open_db(root) as connection:
        for memo in storage.list_company_memos(connection, ticker):
            storage.upsert_memory_entry(
                connection,
                entry_id=f"memo:{memo['id']}",
                ticker=ticker,
                memory_type="memo",
                status="trusted",
                topic=str(memo["title"]),
                as_of=None,
                content=str(memo["content"]),
                source_refs_json=str(memo["source_refs_json"]),
                origin_ref=f"company_memo:{memo['id']}",
                content_hash=hashlib.sha256(str(memo["content"]).encode("utf-8")).hexdigest(),
                created_at=storage.parse_iso(str(memo["updated_at"])),
            )
        for run in storage.list_runs(connection, ticker):
            if run["status"] != "completed":
                continue
            directory = storage.run_dir(root, ticker, storage.parse_iso(str(run["created_at"])), str(run["id"]))
            report_path = directory / "company.md"
            if not report_path.is_file():
                continue
            content = report_path.read_text(encoding="utf-8")[:30000]
            source_refs = [
                {"source_id": item["id"], "locator": "formal_report_source", "relation": "context"}
                for item in storage.get_run_sources(connection, str(run["id"]))
            ]
            storage.upsert_memory_entry(
                connection,
                entry_id=f"formal_report:{run['id']}",
                ticker=ticker,
                memory_type="formal_report",
                status="trusted",
                topic=f"正式公司研究 {run['id']}",
                as_of=str(run["as_of"]),
                content=content,
                source_refs_json=json.dumps(source_refs, ensure_ascii=False),
                origin_ref=f"research_run:{run['id']}",
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                created_at=storage.parse_iso(str(run["completed_at"] or run["updated_at"])),
            )


def _run_directory(root: Path, run: dict[str, Any]) -> Path:
    return storage.run_dir(
        root,
        str(run["ticker"]),
        storage.parse_iso(str(run["created_at"])),
        str(run["id"]),
    )


def _search_sources(root: Path, rows: list[dict[str, Any]], query: str, limit: int) -> dict[str, Any]:
    needle = query.casefold().strip()
    if not needle:
        raise AgentError("empty_search_query", "搜索词不能为空", exit_code=2)
    results: list[dict[str, Any]] = []
    for row in rows:
        source = json.loads(row["source_json"])
        text_path = source.get("text_path")
        if not text_path:
            continue
        path = root / str(text_path)
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if needle in line.casefold():
                start = max(0, index - 2)
                end = min(len(lines), index + 3)
                results.append(
                    {
                        "source_id": row["id"],
                        "title": source.get("title"),
                        "locator": f"lines {start + 1}-{end}",
                        "text": "\n".join(lines[start:end]),
                    }
                )
                break
        if len(results) >= max(1, min(limit, MAX_SEARCH_RESULTS)):
            break
    return {"query": query, "results": results, "truncated": len(results) >= limit}


def _execute_tool(root: Path, row: dict[str, Any], tool: str, arguments: object) -> dict[str, Any]:
    if tool not in TOOL_ARGS:
        raise AgentError("unknown_tool", f"不允许使用工具：{tool}", exit_code=2)
    parsed = _validate_model(TOOL_ARGS[tool], arguments, "invalid_tool_arguments")
    ticker = str(row["ticker"])
    run_id = str(row["run_id"]) if row.get("run_id") else None
    with storage.open_db(root) as connection:
        run = storage.get_run(connection, run_id) if run_id else None
        source_rows = _source_rows(connection, ticker, run_id)
        if tool == "get_run_status":
            if not run:
                return {"ticker": ticker, "run_id": None, "message": "没有可用的研究 run"}
            return research.get_status(root, run_id)  # type: ignore[arg-type]
        if tool == "list_sources":
            return {"ticker": ticker, "run_id": run_id, "sources": [_source_summary(item) for item in source_rows]}
        if tool == "read_source":
            if not run_id:
                raise AgentError("no_run_context", "当前 session 没有可读取来源的 run", exit_code=2)
            args = parsed  # type: ignore[assignment]
            assert isinstance(args, SourceReadArgs)
            return research.read_source(
                root,
                run_id,
                str(args.source_id),
                page=args.page,
                start_line=args.start_line,
                limit_lines=args.limit_lines,
            )
        if tool == "search_archived_sources":
            args = parsed  # type: ignore[assignment]
            assert isinstance(args, SearchArgs)
            return _search_sources(root, source_rows, args.query, args.max_results)
        if tool == "read_report":
            if not run or run["status"] != "completed":
                raise AgentError("report_unavailable", "当前没有已完成的正式报告", exit_code=2)
            path = _run_directory(root, run) / "company.md"
            if not path.is_file():
                raise AgentError("report_missing", "正式报告文件不存在", exit_code=1)
            text = path.read_text(encoding="utf-8")
            return {"run_id": run_id, "path": storage.relative_path(root, path), "text": text[:20000]}
        if tool == "list_memos":
            return {"ticker": ticker, "memos": [_memo_summary(item) for item in storage.list_company_memos(connection, ticker)]}
        if tool == "query_archived_financials":
            args = parsed  # type: ignore[assignment]
            assert isinstance(args, FinancialQueryArgs)
            return analysis_workspace.archived_financials(
                root, row, args.query, limit=args.max_records
            )
        if tool == "run_trusted_calculation":
            args = parsed  # type: ignore[assignment]
            assert isinstance(args, TrustedCalculationArgs)
            return analysis_workspace.trusted_calculation(root, row, args.metric)
        if tool == "create_analysis_dataset":
            args = parsed  # type: ignore[assignment]
            assert isinstance(args, AnalysisDatasetArgs)
            return analysis_workspace.create_dataset(root, row, args.query, limit=args.max_records)
        if tool == "run_analysis_code":
            args = parsed  # type: ignore[assignment]
            assert isinstance(args, AnalysisCodeArgs)
            return analysis_workspace.run_code(root, row, str(args.analysis_id), args.code)
        if tool == "read_analysis_result":
            args = parsed  # type: ignore[assignment]
            assert isinstance(args, AnalysisResultArgs)
            return analysis_workspace.read_result(root, row, str(args.analysis_id))
        args = parsed  # type: ignore[assignment]
        assert isinstance(args, MemoReadArgs)
        memo = storage.get_company_memo(connection, str(args.memo_id))
        if memo is None or memo["ticker"] != ticker:
            raise AgentError("unknown_memo", "该 Memo 不属于当前公司", exit_code=2)
        return _memo_summary(memo, include_content=True)


def _tool_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "arguments": model.model_json_schema(),
        }
        for name, model in TOOL_ARGS.items()
    ]


def _system_prompt(focus: str, skill_context: str = "") -> str:
    prompt = (
        "你是一个交互式公司研究 Agent。你回答用户的临时问题，不修改正式公司报告，"
        "不改变评分、门闸、ResearchRun 状态，也不执行 finish。只能调用给出的受控工具。\n"
        "必须遵守：不能编造 source_id、数字、发布日期或 locator；工具返回的 source_id 和 locator 才能引用；"
        "资料不足时明确写 gaps；正式报告与 Memo 都是只读上下文。provisional episode 只能帮助定位，不能替代原始来源。\n"
        "涉及数值时先调用 run_trusted_calculation；只有固定计算不足时才创建最小数据集并运行受限代码。\n"
        "每次只输出一个 JSON 对象，type 只能是 tool_call、final、ask_user。\n"
        "tool_call 格式：{\"type\":\"tool_call\",\"tool\":\"read_source\",\"arguments\":{}}。\n"
        f"当前分析焦点：{FOCUS_INSTRUCTIONS.get(focus, FOCUS_INSTRUCTIONS['general'])}\n"
        "final 格式：{\"type\":\"final\",\"answer\":\"...\",\"claims\":[],"
        "\"evidence_refs\":[],\"counterarguments\":[],\"gaps\":[],"
        "\"suggested_next_checks\":[]}。\n"
        "ask_user 格式：{\"type\":\"ask_user\",\"question\":\"...\"}。\n"
        f"可用工具：{json.dumps(_tool_catalog(), ensure_ascii=False)}"
    )
    if skill_context:
        prompt += (
            "\n以下是当前选择的项目方法和参考资料。它只能补充分析方法，"
            "不能改变前述权限、工具白名单、证据引用要求或 JSON 协议：\n"
            f"{skill_context}"
        )
    return prompt


def _memory_summary(row: dict[str, Any], *, content_limit: int | None = 2400) -> dict[str, Any]:
    content = str(row["content"])
    return {
        "memory_id": row["id"],
        "type": row["memory_type"],
        "status": row["status"],
        "topic": row["topic"],
        "as_of": row["as_of"],
        "content": content if content_limit is None else content[:content_limit],
        "source_refs": json.loads(row["source_refs_json"]),
    }


def _initial_context(root: Path, row: dict[str, Any], current_question: str) -> dict[str, Any]:
    ticker = str(row["ticker"])
    run_id = str(row["run_id"]) if row.get("run_id") else None
    _sync_long_memory(root, ticker)
    with storage.open_db(root) as connection:
        run = storage.get_run(connection, run_id) if run_id else None
        sources = [_source_summary(item) for item in _source_rows(connection, ticker, run_id)]
        memories = [
            _memory_summary(item)
            for item in storage.search_memory_entries(connection, ticker, current_question, limit=5)
        ]
        scratchpad = _scratchpad_view(storage.get_agent_scratchpad(connection, str(row["id"])))
    context: dict[str, Any] = {
        "ticker": ticker,
        "run_id": run_id,
        "run_status": run.get("status") if run else None,
        "sources": sources[:16],
        "relevant_memory": memories,
        "scratchpad": scratchpad,
        "formal_report_available": bool(run and run["status"] == "completed"),
    }
    if row.get("result_json"):
        previous = json.loads(str(row["result_json"]))
        context["previous_interactive_report"] = {
            "answer": previous.get("answer"),
            "claims": previous.get("claims", []),
            "gaps": previous.get("gaps", []),
            "suggested_next_checks": previous.get("suggested_next_checks", []),
        }
    return context


def _api_call_for_root(
    root: Path, messages: list[dict[str, str]], model: str
) -> tuple[dict[str, Any], int]:
    try:
        envfile.load_env_file(root / ".env")
        response = writer._request_deepseek(
            api_key=writer._api_key(),
            model=model,
            messages=messages,
            temperature=writer.DEFAULT_TEMPERATURE,
            thinking=writer.DEFAULT_THINKING,
        )
        content = writer._extract_content(response)
        usage = writer._usage_summary(response) or {}
        return json.loads(content), int(usage.get("total_tokens", 0))
    except writer.WriterError as error:
        raise AgentError(error.code, error.message, exit_code=error.exit_code) from error
    except json.JSONDecodeError as error:
        raise AgentError("invalid_decision", "Agent 输出不是合法 JSON", exit_code=1) from error


def _validate_evidence_refs(root: Path, row: dict[str, Any], refs: list[EvidenceRef]) -> None:
    ticker = str(row["ticker"])
    run_id = str(row["run_id"]) if row.get("run_id") else None
    with storage.open_db(root) as connection:
        for ref in refs:
            source = storage.get_source(connection, source_id_text(ref.source_id))
            if source is None or source["ticker"] != ticker:
                raise AgentError(
                    "unknown_source_reference",
                    f"Agent 引用了不属于当前公司的 source_id：{ref.source_id}",
                    exit_code=2,
                )
            if run_id and not storage.is_source_linked(connection, run_id, source_id_text(ref.source_id)):
                raise AgentError(
                    "unlinked_source_reference",
                    f"Agent 引用了不属于当前 run 的 source_id：{ref.source_id}",
                    exit_code=2,
                )


def _append_event(root: Path, session_id: str, event_type: str, payload: object) -> None:
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.append_agent_event(
            connection,
            session_id,
            event_type,
            _compact(payload),
            storage.now_utc(),
        )


def _mark_session(
    root: Path,
    session_id: str,
    *,
    status: str,
    iteration: int | None = None,
    used_tokens: int | None = None,
    result: InteractiveReport | None = None,
    error: AgentError | None = None,
) -> None:
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.update_agent_session(
            connection,
            session_id,
            status=status,
            iteration=iteration,
            used_tokens=used_tokens,
            result_json=result.model_dump_json() if result else None,
            error_json=json.dumps({"code": error.code, "message": error.message}, ensure_ascii=False)
            if error
            else None,
            updated_at=storage.now_utc(),
        )
        if result:
            storage.insert_interactive_report(
                connection,
                session_id=session_id,
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
                entry_id=f"episode:{session_id}",
                ticker=result.ticker,
                memory_type="episode",
                status="provisional",
                topic=result.user_question[:500],
                as_of=None,
                content=json.dumps(episode, ensure_ascii=False),
                source_refs_json=json.dumps(
                    [item.model_dump(mode="json") for item in result.evidence_refs], ensure_ascii=False
                ),
                origin_ref=f"interactive_report:{session_id}",
                content_hash=_hash_json(episode),
                created_at=result.created_at,
            )


def _decision(payload: object) -> AgentDecision:
    try:
        return AgentDecision.model_validate(payload)
    except ValidationError as error:
        raise AgentError("invalid_decision", f"Agent 决策不符合协议：{_json_error_details(error)}", exit_code=1) from error


def _execute_session(
    root: Path,
    session_id: str,
    current_question: str,
    *,
    skill_context: str = "",
    on_progress: progress.ProgressCallback | None = None,
) -> dict[str, Any]:
    row = _session(root, session_id)
    question = _session_question(row)
    model = str(row["model"])
    used_tokens = int(row["used_tokens"])
    initial_iteration = int(row["iteration"])
    context = _initial_context(root, row, current_question)
    memory_ids = [
        str(item.get("memory_id"))
        for item in context.get("relevant_memory", [])
        if isinstance(item, dict) and item.get("memory_id")
    ]
    if memory_ids:
        _append_event(root, session_id, "memory_selection", {"memory_ids": memory_ids})
    if question.allow_network:
        error = AgentError(
            "network_not_supported",
            "当前交互 Agent 版本只读取已归档资料；请先用固定 collect 流程采集资料",
            exit_code=2,
        )
        _mark_session(root, session_id, status="failed", error=error)
        raise error

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _system_prompt(question.focus, skill_context)},
        {
            "role": "user",
            "content": (
                f"当前问题：{current_question}\n"
                f"当前上下文：{json.dumps(context, ensure_ascii=False)}\n"
                "如果需要事实，先调用只读工具，再给出 final。"
            ),
        },
    ]
    seen_calls: set[str] = set()
    max_iterations = int(row["max_iterations"])
    token_budget = int(row["token_budget"])

    try:
        for offset in range(max_iterations):
            if used_tokens >= token_budget:
                raise AgentError("agent_budget_exhausted", "Agent 已达到 token 预算", exit_code=1)
            iteration = initial_iteration + offset + 1
            progress.emit(
                on_progress,
                "agent",
                "running",
                f"正在进行第 {iteration}/{max_iterations} 轮分析",
                session_id=session_id,
            )
            messages = _compress_messages(root, session_id, current_question, messages)
            payload, consumed = _api_call_for_root(root, messages, model)
            used_tokens += consumed
            decision = _decision(payload)
            _append_event(
                root,
                session_id,
                "decision",
                {"iteration": iteration, "type": decision.type, "tool": decision.tool},
            )
            with storage.project_lock(root), storage.open_db(root) as connection:
                storage.update_agent_session(
                    connection,
                    session_id,
                    status="active",
                    iteration=iteration,
                    used_tokens=used_tokens,
                    updated_at=storage.now_utc(),
                )

            if decision.type == "final":
                refs = list(decision.evidence_refs)
                for claim in decision.claims:
                    refs.extend(claim.evidence)
                _validate_evidence_refs(root, row, refs)
                report = InteractiveReport(
                    session_id=UUID(session_id),
                    ticker=str(row["ticker"]),
                    user_question=current_question,
                    answer=str(decision.answer),
                    claims=decision.claims,
                    evidence_refs=decision.evidence_refs,
                    counterarguments=decision.counterarguments,
                    gaps=decision.gaps,
                    suggested_next_checks=decision.suggested_next_checks,
                    created_at=storage.now_utc(),
                )
                _mark_session(
                    root,
                    session_id,
                    status="completed",
                    iteration=iteration,
                    used_tokens=used_tokens,
                    result=report,
                )
                _complete_scratchpad(root, session_id, current_question)
                progress.emit(
                    on_progress,
                    "agent",
                    "succeeded",
                    "交互分析完成，已生成临时报告",
                    session_id=session_id,
                )
                return {
                    "session_id": session_id,
                    "ticker": report.ticker,
                    "run_id": row["run_id"],
                    "status": "completed",
                    "report": json.loads(report.model_dump_json()),
                }

            if decision.type == "ask_user":
                _mark_session(
                    root,
                    session_id,
                    status="waiting_user",
                    iteration=iteration,
                    used_tokens=used_tokens,
                )
                progress.emit(
                    on_progress,
                    "agent",
                    "waiting_user",
                    "Agent 需要用户补充信息",
                    session_id=session_id,
                )
                return {
                    "session_id": session_id,
                    "ticker": row["ticker"],
                    "run_id": row["run_id"],
                    "status": "waiting_user",
                    "question": decision.question,
                }

            call_key = json.dumps(
                {"tool": decision.tool, "arguments": decision.arguments},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if call_key in seen_calls:
                raise AgentError("repeated_tool_call", "Agent 重复调用相同工具，已停止本次会话", exit_code=1)
            seen_calls.add(call_key)
            progress.emit(
                on_progress,
                "agent.tool",
                "running",
                f"正在读取工具：{decision.tool}",
                session_id=session_id,
            )
            try:
                result = _execute_tool(root, row, str(decision.tool), decision.arguments)
            except analysis_workspace.AnalysisWorkspaceError as error:
                raise AgentError(error.code, error.message, exit_code=error.exit_code) from error
            _append_event(
                root,
                session_id,
                "tool_result",
                {"iteration": iteration, "tool": decision.tool, "result": result},
            )
            _record_tool_observation(root, session_id, current_question, str(decision.tool), result)
            messages.append(
                {
                    "role": "user",
                    "content": f"工具 {decision.tool} 返回：{_compact(result)}\n请继续处理原问题。",
                }
            )
        raise AgentError("agent_iteration_exhausted", "Agent 已达到最大迭代次数", exit_code=1)
    except AgentError as error:
        _mark_session(
            root,
            session_id,
            status="failed" if error.code != "agent_budget_exhausted" else "budget_exhausted",
            iteration=initial_iteration,
            used_tokens=used_tokens,
            error=error,
        )
        raise


def _resolved_model(model: str | None) -> str:
    return model or writer._resolved_model(None)


def _session_skill_context(root: Path, session_id: str) -> str:
    """续问时按已审计的 Skill 名称重新加载正文，拒绝悄然变更的内容。"""
    with storage.open_db(root) as connection:
        events = storage.list_agent_events(connection, session_id)
    selection: list[dict[str, object]] | None = None
    for event in reversed(events):
        if event["event_type"] != "skill_selection":
            continue
        try:
            payload = json.loads(str(event["payload_json"]))
        except json.JSONDecodeError:
            continue
        raw_skills = payload.get("skills") if isinstance(payload, dict) else None
        if isinstance(raw_skills, list) and all(isinstance(item, dict) for item in raw_skills):
            selection = [dict(item) for item in raw_skills]
            break
    if not selection:
        return ""
    names = [str(item.get("name") or "") for item in selection]
    if any(not name for name in names):
        raise AgentError("invalid_skill_selection", "Agent session 的 Skill 审计记录损坏", exit_code=2)
    try:
        loaded = skill_registry.load_selected(root, names)
    except skill_registry.SkillRegistryError as error:
        raise AgentError(error.code, error.message, exit_code=2) from error
    if skill_registry.selection_audit(loaded) != selection:
        raise AgentError("skill_changed_since_selection", "Skill 内容已在会话期间改变；请重新发起临时分析", exit_code=2)
    context = skill_registry.execution_context(loaded)
    if len(context) > 30000:
        raise AgentError("skill_context_too_large", "选择的 Skill 正文和参考资料超过交互 Agent 上下文上限", exit_code=2)
    return context


def ask(
    root: Path,
    ticker: str,
    question: InteractiveQuestion,
    *,
    model: str | None = None,
    skill_context: str = "",
    skill_audit: list[dict[str, object]] | None = None,
    on_progress: progress.ProgressCallback | None = None,
) -> dict[str, Any]:
    ticker = research._checked_ticker(ticker)
    if question.allow_network:
        raise AgentError(
            "network_not_supported",
            "当前交互 Agent 只支持已归档资料；请先用固定 collect 流程",
            exit_code=2,
        )
    if len(skill_context) > 30000:
        raise AgentError("skill_context_too_large", "选择的 Skill 正文和参考资料超过交互 Agent 上下文上限", exit_code=2)
    storage.initialize(root)
    session_id = str(uuid4())
    resolved_model = _resolved_model(model)
    created_at = storage.now_utc()
    with storage.project_lock(root), storage.open_db(root) as connection:
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
    _append_event(root, session_id, "question", {"question": question.question})
    _ensure_scratchpad(root, session_id, question.question)
    if skill_audit:
        _append_event(root, session_id, "skill_selection", {"skills": skill_audit})
    try:
        return _execute_session(
            root, session_id, question.question, skill_context=skill_context, on_progress=on_progress
        )
    except AgentError as error:
        if error.code in {"missing_api_key", "unauthorized"}:
            raise
        raise


def followup(
    root: Path,
    session_id: str,
    request: InteractiveFollowup,
    *,
    on_progress: progress.ProgressCallback | None = None,
) -> dict[str, Any]:
    row = _session(root, session_id)
    if row["status"] == "failed":
        raise AgentError("session_failed", "失败的 session 不能继续，请重新 ask", exit_code=2)
    _append_event(root, session_id, "followup", {"question": request.question})
    _ensure_scratchpad(root, session_id, request.question)
    return _execute_session(
        root,
        session_id,
        request.question,
        skill_context=_session_skill_context(root, session_id),
        on_progress=on_progress,
    )


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
    if row["result_json"]:
        result["report"] = json.loads(row["result_json"])
    if row["error_json"]:
        result["error"] = json.loads(row["error_json"])
    with storage.open_db(root) as connection:
        result["event_count"] = len(storage.list_agent_events(connection, session_id))
        scratchpad = _scratchpad_view(storage.get_agent_scratchpad(connection, session_id))
        result["scratchpad"] = scratchpad
        analysis = storage.list_analysis_runs(connection, session_id)
        result["analysis_runs"] = [
            {
                "analysis_id": item["id"],
                "execution_index": item["execution_index"],
                "status": item["status"],
                "dataset_hash": item["dataset_hash"],
                "code_hash": item["code_hash"],
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


def _validate_memo_sources(root: Path, row: dict[str, Any], request: MemoRequest) -> None:
    ticker = str(row["ticker"])
    run_id = str(row["run_id"]) if row.get("run_id") else None
    with storage.open_db(root) as connection:
        for reference in request.source_refs:
            source = storage.get_source(connection, source_id_text(reference.source_id))
            if source is None or source["ticker"] != ticker:
                raise AgentError("unknown_source_reference", "Memo 引用了未知或其他公司的来源", exit_code=2)
            if run_id and not storage.is_source_linked(connection, run_id, source_id_text(reference.source_id)):
                raise AgentError("unlinked_source_reference", "Memo 来源不属于该 session 的 run", exit_code=2)


def memo_save(root: Path, session_id: str, request: MemoRequest) -> dict[str, Any]:
    row = _session(root, session_id)
    with storage.open_db(root) as connection:
        report = storage.get_interactive_report(connection, session_id)
    if report is None:
        raise AgentError("report_unavailable", "只有已生成临时报告的 session 才能保存 Memo", exit_code=2)
    _validate_memo_sources(root, row, request)
    memo_id = str(uuid4())
    now = storage.now_utc()
    with storage.project_lock(root), storage.open_db(root) as connection:
        version = storage.next_memo_version(connection, str(row["ticker"]), request.title)
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
            tags_json=json.dumps(request.tags, ensure_ascii=False),
            version=version,
            created_at=now,
        )
    _sync_long_memory(root, str(row["ticker"]))
    memo = CompanyMemo(
        memo_id=UUID(memo_id),
        ticker=str(row["ticker"]),
        title=request.title,
        content=request.content,
        source_refs=request.source_refs,
        created_from_session=UUID(session_id),
        tags=request.tags,
        version=version,
        created_at=now,
        updated_at=now,
    )
    return {"saved": True, "memo": json.loads(memo.model_dump_json())}


def memo_list(root: Path, ticker: str) -> dict[str, Any]:
    ticker = research._checked_ticker(ticker)
    storage.initialize(root)
    with storage.open_db(root) as connection:
        rows = storage.list_company_memos(connection, ticker)
    return {"ticker": ticker, "memos": [_memo_summary(row, include_content=False) for row in rows]}


def memo_read(root: Path, memo_id: str) -> dict[str, Any]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        row = storage.get_company_memo(connection, memo_id)
    if row is None:
        raise AgentError("unknown_memo", f"找不到 Memo：{memo_id}", exit_code=2)
    return _memo_summary(row, include_content=True)


def memory_search(root: Path, ticker: str, query: str = "") -> dict[str, Any]:
    ticker = research._checked_ticker(ticker)
    storage.initialize(root)
    _sync_long_memory(root, ticker)
    with storage.open_db(root) as connection:
        entries = storage.search_memory_entries(connection, ticker, query, limit=20)
    return {"ticker": ticker, "query": query, "memories": [_memory_summary(item) for item in entries]}


def memory_read(root: Path, entry_id: str) -> dict[str, Any]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        entry = storage.get_memory_entry(connection, entry_id)
    if entry is None:
        raise AgentError("unknown_memory", f"找不到记忆：{entry_id}", exit_code=2)
    return _memory_summary(entry, content_limit=None)
