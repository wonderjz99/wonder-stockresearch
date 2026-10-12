"""交互 Agent 的可检查工作记忆、上下文压缩与长期记忆投影。"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from wonder_stockresearch import storage

DEFAULT_MAX_RESULT_CHARS = 12000
MAX_CONTEXT_CHARS = 40000


def estimate_input_tokens(messages: list[dict[str, str]]) -> int:
    """轻量估算，不把字符数当 token 数；实际消耗仍以 API usage 为准。"""
    return sum(
        math.ceil(sum(0.4 if char.isascii() else 1.0 for char in item["content"])) + 32
        for item in messages
    )


def tool_context(result: dict[str, Any], *, token_limit: int = 1500) -> str:
    """给模型的有界 JSON 投影；完整工具结果/证据回执仍由执行层处理。"""
    view = dict(result)
    # 覆盖摘要在独立字段保留，不让长诊断说明挤掉字段目录和实际事实。
    if isinstance(view.get("coverage_diagnostic"), dict):
        view["coverage_diagnostic"] = {
            key: value for key, value in view["coverage_diagnostic"].items() if key != "note"
        }
    row_refs = {
        item["fact_id"]: item
        for item in result.get("citable_refs", [])
        if isinstance(item, dict) and isinstance(item.get("fact_id"), str)
    }
    align_refs = bool(row_refs) and isinstance(view.get("rows"), list)
    for key in ("dataset_hash", "sql_hash", "snapshot_hash", "source_sha256", "function_hash"):
        view.pop(key, None)
    for key in ("result", "sql"):
        if isinstance(view.get(key), dict):
            view[key] = json.loads(tool_context(view[key], token_limit=max(128, token_limit - 250)))
    for key in ("citable_refs", "source_refs"):
        if isinstance(view.get(key), list):
            view[key] = [
                {
                    field: item[field]
                    for field in ("source_id", "locator", "relation", "ref_id")
                    if field in item
                }
                for item in view[key]
                if isinstance(item, dict)
            ]
    if isinstance(view.get("records"), list):
        view["records"] = [
            {
                key: value
                for key, value in item.items()
                if key not in {"source_sha256", "value_text"}
                and not (key == "value_decimal" and "value" in item)
            }
            if isinstance(item, dict)
            else item
            for item in view["records"]
        ]
    if isinstance(view.get("sources"), list):
        view["sources"] = [
            {
                **{key: item.get(key) for key in ("source_id", "kind", "title")},
                **{key: item.get("metadata", {}).get(key) for key in ("dataset", "role")},
            }
            for item in view["sources"]
        ]
    if isinstance(view.get("text"), str) and len(view["text"]) > 2000:
        view["text"] = view["text"][:2000]
        view["text_truncated"] = True
    # SQL 的 rows 与 citable_refs 重复携带定位；只投影对应行的引用，完整回执仍保留。
    if isinstance(view.get("citable_refs"), list):
        refs = view["citable_refs"]
        view["citable_refs"] = refs[:8]
        if len(refs) > 8:
            view["citable_refs_omitted"] = len(refs) - 8
    while True:
        if align_refs:
            visible = [
                row_refs[item["fact_id"]]
                for item in view["rows"]
                if isinstance(item, dict) and item.get("fact_id") in row_refs
            ]
            view["citable_refs"] = [
                {
                    key: ref[key]
                    for key in ("source_id", "locator", "relation", "ref_id")
                    if key in ref
                }
                for ref in visible[:8]
            ]
            view["citable_refs_omitted"] = max(0, len(row_refs) - len(view["citable_refs"]))
        encoded = json.dumps(view, ensure_ascii=False, separators=(",", ":"), default=str)
        if estimate_input_tokens([{"content": encoded}]) <= token_limit:
            return encoded
        for key in (
            "citable_refs",
            "source_refs",
            "records",
            "sources",
            "rows",
            "memos",
            "field_catalog",
        ):
            if key == "citable_refs" and align_refs:
                continue
            if isinstance(view.get(key), list) and len(view[key]) > 1:
                view[key] = view[key][:-1]
                view[f"{key}_omitted"] = int(view.get(f"{key}_omitted", 0)) + 1
                view["context_truncated"] = True
                break
        else:
            # 单条事实不截半截；过大时走现有的消息压缩/预算停止。
            return encoded


def compact(value: object, limit: int = DEFAULT_MAX_RESULT_CHARS) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…（结果已截断）"


def hash_json(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def scratchpad_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
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
            content_hash=hash_json(body),
            compression_count=compression_count,
            updated_at=storage.now_utc(),
        )
    return {"revision": revision, **body}


def ensure_scratchpad(root: Path, session_id: str, objective: str) -> dict[str, Any]:
    with storage.open_db(root) as connection:
        previous = storage.get_agent_scratchpad(connection, session_id)
    view = scratchpad_view(previous)
    if view is not None:
        return view
    return _save_scratchpad(
        root,
        session_id,
        objective=objective,
        plan=[
            "检索归档，缺少数据时按权限补取",
            "用受限 SQL/函数完成所需计算",
            "核对期间、口径和来源后回答",
        ],
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
    return list({(item["source_id"], item["locator"]): item for item in found}.values())


def record_tool_observation(
    root: Path, session_id: str, objective: str, tool: str, result: dict[str, Any]
) -> dict[str, Any]:
    current = ensure_scratchpad(root, session_id, objective)
    observations = [*current["observations"], {"tool": tool, "summary": compact(result, 1400)}]
    source_refs = [*current["source_refs"], *_refs_in(result)]
    unique_refs = {
        (item["source_id"], item["locator"]): item
        for item in source_refs
        if "source_id" in item and "locator" in item
    }
    calculation_refs = list(current["calculation_refs"])
    if tool == "run_trusted_calculation" and isinstance(result.get("calculation"), dict):
        calculation = result["calculation"]
        calculation_refs.append(
            {"metric": calculation.get("metric"), "version": calculation.get("version")}
        )
    if tool in {
        "run_analysis_code",
        "read_analysis_result",
        "run_readonly_sql",
        "run_analysis_function",
        "read_analysis_artifact",
    }:
        calculation_refs.append(
            {"analysis_id": result.get("analysis_id"), "status": result.get("status")}
        )
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


def complete_scratchpad(root: Path, session_id: str, objective: str) -> None:
    current = ensure_scratchpad(root, session_id, objective)
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


def compress_messages(
    root: Path,
    session_id: str,
    objective: str,
    messages: list[dict[str, str]],
    *,
    max_input_tokens: int | None = None,
    working_record: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """字符阈值或剩余预算触发压缩；保留规则、问题及能容纳的最近工具交互。"""
    if sum(len(message["content"]) for message in messages) < int(MAX_CONTEXT_CHARS * 0.7) and (
        max_input_tokens is None or estimate_input_tokens(messages) <= max_input_tokens
    ):
        return messages
    current = ensure_scratchpad(root, session_id, objective)
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
    # 只缩小本次请求投影，不删持久化 Scratchpad 或证据回执。
    view = dict(compressed)
    if working_record is not None:
        from wonder_stockresearch.interactive import agent_working_record

        view["turn_working_record"] = agent_working_record.project(working_record, token_limit=700)
    recent = messages[-2:] if len(messages) > 2 else []
    while True:
        result = [
            messages[0],
            {
                "role": "user",
                "content": (
                    f"原问题及用户补充：{objective}\n"
                    "以下是可检查的工作记忆摘要，观察不等于已证实事实："
                    f"{json.dumps(view, ensure_ascii=False)}\n"
                    "继续调用受控工具或给出 final；provisional episode 不能替代原始证据。"
                    "省略的资料可重新通过受控工具读取。"
                ),
            },
            *recent,
        ]
        if max_input_tokens is None or estimate_input_tokens(result) <= max_input_tokens:
            return result
        # 优先丢旧观察，尽量保留最近工具结果中的末尾数值与 locator。
        for key in ("observations", "source_refs", "calculation_refs", "plan", "open_questions"):
            if view[key]:
                view[key] = view[key][1:]
                break
        else:
            if "turn_working_record" in view and view["turn_working_record"].get(
                "available_input", {}
            ).get("sample"):
                view["turn_working_record"]["available_input"].pop("sample")
                continue
            if len(recent) > 1:
                recent = recent[1:]
            else:
                # 最新结果不能静默消失，避免模型无结果重查。规则/问题/结果不适配时明确停止。
                return result


def sync_long_memory(root: Path, ticker: str) -> None:
    """只从已完成报告和用户明确保存的 Memo 写入可信长期记忆。"""
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
            directory = storage.run_dir(
                root, ticker, storage.parse_iso(str(run["created_at"])), str(run["id"])
            )
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


def memory_summary(row: dict[str, Any], *, content_limit: int | None = 2400) -> dict[str, Any]:
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
