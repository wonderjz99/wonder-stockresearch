"""由本 Turn 的真实执行记录生成工作清单；不是模型推理或证据授权。"""

from __future__ import annotations

import json
from typing import Any


def summarize_result(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "analysis_id",
        "status",
        "mode",
        "filters",
        "row_count",
        "total_matches",
        "returned_records",
        "has_more",
        "next_offset",
        "input_summary",
        "citable",
        "numeric_submission",
        "coverage_diagnostic",
    )
    summary = {key: result[key] for key in keys if key in result}
    if isinstance(result.get("citable_refs"), list):
        summary["citable_refs"] = result["citable_refs"][:6]
    sample = result.get("records", result.get("rows", []))
    if isinstance(sample, list):
        fields = {
            "field_key",
            "dataset",
            "label",
            "period",
            "value_decimal",
            "value",
            "unit",
            "scale",
            "currency",
            "fact_id",
            "source_id",
            "locator",
            "periods",
            "period_count",
            "periods_omitted",
            "earliest_period",
            "latest_period",
        }
        summary["sample"] = [
            {key: value for key, value in row.items() if key in fields}
            for row in sample[:6]
            if isinstance(row, dict)
        ]
    if tool in {"run_analysis_function", "run_trusted_calculation", "run_analysis_code"}:
        summary["result"] = result.get("result", result.get("calculation"))
    # 用既有模型投影缩小超大结果，完整数字仍在受控产物中。
    from wonder_stockresearch.interactive import agent_results

    return json.loads(agent_results.model_projection(summary, token_limit=1000))


def build(
    events: list[dict[str, Any]], turn_id: str, question: str, *, status: str | None = None
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "turn_id": turn_id,
        "objective": question,
        "completed": [],
        "pending": [],
        "note": "工作清单是执行定位；样本不是完整输入，引用仍须本 Turn 回执校验。",
    }
    operations = []
    for event in events:
        if event.get("turn_id") != turn_id:
            continue
        try:
            payload = json.loads(event["payload_json"])
        except (ValueError, TypeError):
            continue
        kind = event["event_type"]
        if kind == "question_requirements":
            record["question_requirements"] = payload
        if kind == "tool_result":
            tool = payload.get("tool", "")
            result = payload.get("working_observation")
            if not isinstance(result, dict):
                result = summarize_result(tool, payload.get("result", {}))
            operations.append({"tool": tool, "status": "success", "observed": result})
            if tool == "query_company_data" and result.get("mode") == "fields":
                record["field_selection"] = result
            if tool in {"run_readonly_sql", "query_company_data", "query_archived_financials"} and (
                result.get("analysis_id") or result.get("mode") != "fields"
            ):
                if result.get("row_count") == 0 or result.get("returned_records") == 0:
                    record["empty_query"] = result
                else:
                    record["available_input"] = result
            if tool in {"run_analysis_function", "run_trusted_calculation", "run_analysis_code"}:
                record["calculation"] = result
                record.pop("last_error", None)
        elif kind in {"tool_error", "tool_blocked"}:
            error = {
                key: payload[key]
                for key in (
                    "tool",
                    "code",
                    "message",
                    "arguments_hash",
                    "analysis_id",
                    "executed",
                    "node",
                    "line",
                    "column",
                    "next_step_hint",
                    "final_only",
                )
                if key in payload
            }
            record["last_error"] = error
            operations.append({"status": "blocked" if kind == "tool_blocked" else "error", **error})
        elif kind == "tool_final_only":
            record["final_only"] = True
    if record.get("field_selection", {}).get("total_matches", 0):
        record["completed"].append("已定位候选字段，需核对原值/同比及口径")
    if record.get("available_input"):
        record["completed"].append("已取得查询输入；使用现有产物，不重复采集")
    if record.get("calculation"):
        record["completed"].append("已完成计算，按实际期间/单位/引用形成回答")
    if record.get("final_only"):
        record["pending"].append("只用已有事实收尾，不再调用工具；未完成计算明确说明")
    elif record.get("last_error"):
        record["pending"].append("先处理 last_error；不能把执行失败当成数据缺失")
    elif record.get("available_input") and not record.get("calculation"):
        record["pending"].append("纠正当前错误并计算；单位未知时保留未知")
    elif not record.get("calculation") and not record.get("available_input"):
        record["pending"].append("先查准确字段，再选择所需期间的数据")
    record["pending"].append("形成回答，未完成部分写 gaps")
    if status == "succeeded":
        record["completed"].append("已生成临时回答；结论质量仍需核对")
        record["pending"] = []
    record["recent_operations"] = operations[-3:]
    return record


def project(record: dict[str, Any], token_limit: int = 1700) -> dict[str, Any]:
    """先缩样本和旧操作，保留输入定位、完成状态和当前错误。"""
    from wonder_stockresearch.interactive import agent_memory

    view = json.loads(json.dumps(record, ensure_ascii=False, default=str))
    view.pop("recent_operations", None)
    view["objective"] = str(view.get("objective", ""))[:400]
    while (
        agent_memory.estimate_input_tokens([{"content": json.dumps(view, ensure_ascii=False)}])
        > token_limit
    ):
        reduced = False
        for key in ("field_selection", "available_input", "calculation"):
            item = view.get(key, {})
            for field in ("sample", "input_summary", "filters", "result"):
                if field in item:
                    item.pop(field)
                    item["details_omitted"] = True
                    reduced = True
                    break
            if reduced:
                break
            refs = item.get("citable_refs", [])
            if refs:
                item["citable_refs"] = refs[:-1]
                item["citable_refs_omitted"] = item.get("citable_refs_omitted", 0) + 1
                reduced = True
                break
        if not reduced:
            if len(view["objective"]) > 80:
                view["objective"] = view["objective"][:80] + "…（原问题另见用户消息）"
            else:
                break
    return view
