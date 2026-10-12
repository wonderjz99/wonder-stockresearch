"""交互 Agent 的受控 Tool handler 与只读数据访问。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wonder_stockresearch import storage
from wonder_stockresearch.fixed import research
from wonder_stockresearch.interactive import (
    agent_company,
    agent_data,
    analysis_workspace,
    query_workspace,
)
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.interactive.tool_contracts import (
    AnalysisArtifactArgs,
    AnalysisCodeArgs,
    AnalysisDatasetArgs,
    AnalysisFunctionArgs,
    AnalysisResultArgs,
    CompanyQueryArgs,
    EmptyArgs,
    FetchFinancialArgs,
    FinancialQueryArgs,
    MemoReadArgs,
    ReadonlySqlArgs,
    SearchArgs,
    SourceReadArgs,
    ToolContext,
    TrustedCalculationArgs,
)

MAX_SEARCH_RESULTS = 5


def source_rows(connection: Any, ticker: str, run_id: str | None) -> list[dict[str, Any]]:
    return (
        storage.get_run_sources(connection, run_id)
        if run_id
        else storage.list_sources_for_ticker(connection, ticker)
    )


def source_summary(row: dict[str, Any]) -> dict[str, Any]:
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


def memo_summary(row: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "memo_id": row["id"],
        "ticker": row["ticker"],
        "title": row["title"],
        "version": row["version"],
        "tags": json.loads(row["tags_json"]),
        "source_refs": json.loads(row["source_refs_json"]),
        "created_from_session": row["created_from_session"],
        "created_from_turn": row.get("created_from_turn"),
        "created_from_report_turn": row.get("created_from_report_turn"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_content:
        data["content"] = row["content"]
    return data


def _run_directory(root: Path, run: dict[str, Any]) -> Path:
    return storage.run_dir(
        root,
        str(run["ticker"]),
        storage.parse_iso(str(run["created_at"])),
        str(run["id"]),
    )


def _search_sources(
    root: Path, rows: list[dict[str, Any]], query: str, limit: int
) -> dict[str, Any]:
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


def validate_context(context: ToolContext) -> None:
    """任何已授权工具都不能跨 Turn 使用另一公司的执行范围。"""
    if context.turn_id is not None:
        with storage.open_db(context.root) as connection:
            agent_company.scope(connection, context.row)


def get_run_status(context: ToolContext, args: EmptyArgs) -> dict[str, Any]:
    with storage.open_db(context.root) as connection:
        run = storage.get_run(connection, context.run_id) if context.run_id else None
    result = (
        {"ticker": context.ticker, "run_id": None, "message": "没有可用的研究 run"}
        if not run
        else research.get_status(context.root, context.run_id)
    )
    for key in ("steps", "draft_sections"):
        if isinstance(result.get(key), (list, dict)):
            result[key + "_summary"] = {"count": len(result.pop(key))}
    return result


def list_sources(context: ToolContext, args: EmptyArgs) -> dict[str, Any]:
    with storage.open_db(context.root) as connection:
        sources = agent_company.context_sources(connection, context.row)
    return {
        "ticker": context.ticker,
        "run_id": context.run_id,
        "sources": [source_summary(item) for item in sources],
    }


def read_source(context: ToolContext, args: SourceReadArgs) -> dict[str, Any]:
    source_id = str(args.source_id)
    with storage.open_db(context.root) as connection:
        scoped = storage.get_source(connection, source_id)
        company_scope = agent_company.scope(connection, context.row)
        if (
            scoped
            and company_scope is not None
            and not agent_company.allowed(connection, context.row, scoped)
        ):
            raise AgentError("unlinked_source_reference", "来源不属于当前公司快照", exit_code=2)
        is_turn_source = scoped and storage.is_agent_turn_source(
            scoped, str(context.row["id"]), context.turn_id
        )
        is_company_source = company_scope is not None and not (
            context.run_id and storage.is_source_linked(connection, context.run_id, source_id)
        )
        if scoped and (is_turn_source or is_company_source):
            agent_company.verify_archive(context.root, scoped)
            path = context.root / str(scoped["text_path"] or scoped["raw_path"])
            text = path.read_text(encoding="utf-8")
            result = research._read_lines(text, args.start_line or 1, args.limit_lines or 80)
            return {**result, "source_id": source_id}
    if not context.run_id:
        raise AgentError("no_run_context", "当前 session 没有可读取来源的 run", exit_code=2)
    return research.read_source(
        context.root,
        context.run_id,
        source_id,
        page=args.page,
        start_line=args.start_line,
        limit_lines=args.limit_lines,
    )


def search_archived_sources(context: ToolContext, args: SearchArgs) -> dict[str, Any]:
    with storage.open_db(context.root) as connection:
        sources = agent_company.context_sources(connection, context.row)
        company_scope = agent_company.scope(connection, context.row)
    if company_scope is not None:
        for source in sources:
            agent_company.verify_archive(context.root, source)
    return _search_sources(context.root, sources, args.query, args.max_results)


def read_report(context: ToolContext, args: EmptyArgs) -> dict[str, Any]:
    with storage.open_db(context.root) as connection:
        run = storage.get_run(connection, context.run_id) if context.run_id else None
    if not run or run["status"] != "completed":
        raise AgentError("report_unavailable", "当前没有已完成的正式报告", exit_code=2)
    path = _run_directory(context.root, run) / "company.md"
    if not path.is_file():
        raise AgentError("report_missing", "正式报告文件不存在", exit_code=1)
    return {
        "run_id": context.run_id,
        "path": storage.relative_path(context.root, path),
        "text": path.read_text(encoding="utf-8")[:20000],
    }


def list_memos(context: ToolContext, args: EmptyArgs) -> dict[str, Any]:
    from wonder_stockresearch.interactive import company_memo

    with storage.open_db(context.root) as connection:
        memos = company_memo.list_for_company(connection, context.ticker)
    return {"ticker": context.ticker, "memos": memos}


def read_memo(context: ToolContext, args: MemoReadArgs) -> dict[str, Any]:
    from wonder_stockresearch.interactive import company_memo

    with storage.open_db(context.root) as connection:
        notebook = company_memo.read(connection, str(args.memo_id))
        if notebook is not None:
            if notebook["ticker"] != context.ticker:
                raise AgentError("unknown_memo", "该 Memo 不属于当前公司", exit_code=2)
            return notebook
        memo = storage.get_company_memo(connection, str(args.memo_id))
    if memo is None or memo["ticker"] != context.ticker:
        raise AgentError("unknown_memo", "该 Memo 不属于当前公司", exit_code=2)
    return memo_summary(memo, include_content=True)


def query_company_data(context: ToolContext, args: CompanyQueryArgs) -> dict[str, Any]:
    return agent_company.query(
        context.root,
        context.row,
        dataset=args.dataset,
        query=args.query,
        period=args.period,
        mode=args.mode,
        limit=args.max_records,
        periods=args.periods,
        period_kind=args.period_kind,
        field_keys=args.field_keys,
        offset=args.offset,
    )


def query_archived_financials(context: ToolContext, args: FinancialQueryArgs) -> dict[str, Any]:
    return analysis_workspace.archived_financials(
        context.root, context.row, args.query, limit=args.max_records
    )


def fetch_financial_data(context: ToolContext, args: FetchFinancialArgs) -> dict[str, Any]:
    return agent_data.fetch_financial_data(
        context.root, context.row, args.dataset, args.query, limit=args.max_records
    )


def run_trusted_calculation(context: ToolContext, args: TrustedCalculationArgs) -> dict[str, Any]:
    return analysis_workspace.trusted_calculation(context.root, context.row, args.metric)


def create_analysis_dataset(context: ToolContext, args: AnalysisDatasetArgs) -> dict[str, Any]:
    return analysis_workspace.create_dataset(
        context.root, context.row, args.query, limit=args.max_records
    )


def run_analysis_code(context: ToolContext, args: AnalysisCodeArgs) -> dict[str, Any]:
    return analysis_workspace.run_code(context.root, context.row, str(args.analysis_id), args.code)


def read_analysis_result(context: ToolContext, args: AnalysisResultArgs) -> dict[str, Any]:
    return analysis_workspace.read_result(context.root, context.row, str(args.analysis_id))


def _require_turn(context: ToolContext) -> str:
    if context.turn_id is None:
        raise AgentError("missing_agent_turn", "QueryWorkspace 缺少当前 Turn", exit_code=1)
    return context.turn_id


def describe_analysis_schema(context: ToolContext, args: EmptyArgs) -> dict[str, Any]:
    return query_workspace.describe_schema(context.root, context.row, _require_turn(context))


def run_readonly_sql(context: ToolContext, args: ReadonlySqlArgs) -> dict[str, Any]:
    return query_workspace.run_readonly_sql(
        context.root, context.row, _require_turn(context), args.sql
    )


def run_analysis_function(context: ToolContext, args: AnalysisFunctionArgs) -> dict[str, Any]:
    return query_workspace.run_analysis_function(
        context.root, context.row, _require_turn(context), str(args.analysis_id), args.code
    )


def read_analysis_artifact(context: ToolContext, args: AnalysisArtifactArgs) -> dict[str, Any]:
    return query_workspace.read_analysis_artifact(
        context.root, context.row, _require_turn(context), str(args.analysis_id)
    )


def _execute_tool_impl(
    root: Path, row: dict[str, Any], tool: str, arguments: object
) -> dict[str, Any]:
    """兼容已有内部调用；普通执行应通过带 allowlist 的 execute_tool。"""
    from wonder_stockresearch.interactive import tool_registry

    if tool_registry.get(tool) is None:
        raise AgentError("unknown_tool", f"未注册工具：{tool}", exit_code=2)
    try:
        return tool_registry.execute(tool, root, row, arguments)
    except tool_registry.ToolRegistryError as error:
        raise AgentError(error.code, error.message, exit_code=2) from error


def execute_tool(
    root: Path,
    row: dict[str, Any],
    tool: str,
    arguments: object,
    allowed_tool_names: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    from wonder_stockresearch.interactive import tool_registry

    try:
        definitions = tool_registry.definitions_for("interactive_research", allowed_tool_names)
        if tool not in {item.tool_id for item in definitions}:
            raise tool_registry.ToolRegistryError(
                "unauthorized_tool", f"当前 Skill/capability 未授权使用工具：{tool}"
            )
        return tool_registry.execute(tool, root, row, arguments)
    except tool_registry.ToolRegistryError as error:
        raise AgentError(error.code, error.message, exit_code=2) from error


def tool_catalog(tool_names: list[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    from wonder_stockresearch.interactive import tool_registry

    try:
        return tool_registry.schema_catalog(tool_names, "interactive_research")
    except tool_registry.ToolRegistryError as error:
        raise AgentError(error.code, error.message, exit_code=2) from error
