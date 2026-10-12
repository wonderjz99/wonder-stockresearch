"""通用数值提交契约：从受信任输入生成表和运算，不采纳模型抄写的值。"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, localcontext
from itertools import pairwise
from pathlib import Path

from wonder_stockresearch import storage
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import AgentDecision, EvidenceRef, NumericBlockRequest

OPERATIONS = ("values", "mean", "range", "population_stddev", "changes")


def verified_facts(snapshot: dict, rows: list[dict]) -> list[dict]:
    """SQL 的别名/聚合列不是原始事实；只按已返回的身份反查快照。"""
    ids = {item.get("fact_id") for item in rows if isinstance(item.get("fact_id"), str)}
    facts = []
    for item in snapshot["financial_facts"]:
        if item["fact_id"] not in ids:
            continue
        value = item.get("value_decimal", item.get("value"))
        try:
            valid = Decimal(str(value)).is_finite()
        except InvalidOperation:
            valid = False
        if valid:
            facts.append({**item, "value_decimal": str(value)})
    return facts


def series_identity(row: dict) -> str:
    dimensions = json.loads(row.get("dimensions_json") or "{}")
    dimensions.pop("fiscal_year", None)
    return json.dumps(
        {
            key: row.get(key)
            for key in (
                "field_key",
                "label",
                "dataset",
                "period_kind",
                "unit",
                "scale",
                "currency",
                "accounting_standard",
                "accounting_scope",
            )
        }
        | {
            "dimensions": dimensions,
            "unknown_unit_source": row.get("source_id")
            if row.get("unit") is None or row.get("scale") is None
            else None,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def calculate(rows: list[dict], operations: list[str]) -> list[dict]:
    """普通 Decimal 函数适用于任意同口径序列；不包含公司/指标路由。"""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(series_identity(row), []).append(row)
    output = []
    for identity, values in grouped.items():
        ordered = sorted(values, key=lambda item: str(item.get("period") or ""))
        periods = [item.get("period") for item in ordered]
        if len(set(periods)) != len(periods):
            raise AgentError(
                "ambiguous_numeric_series",
                "同口径存在重复期间；请按来源/修订版本缩小输入",
                exit_code=2,
            )
        numbers = [Decimal(item["value_decimal"]) for item in ordered]
        with localcontext() as ctx:
            ctx.prec = 42
            mean = sum(numbers) / len(numbers)
            results = {}
            if "mean" in operations:
                results["mean"] = str(mean)
            if "range" in operations:
                results["range"] = str(max(numbers) - min(numbers))
            if "population_stddev" in operations:
                results["population_stddev"] = str(
                    (sum((n - mean) ** 2 for n in numbers) / len(numbers)).sqrt()
                )
            changes = []
            if "changes" in operations:
                for before, after in pairwise(ordered):
                    delta = Decimal(after["value_decimal"]) - Decimal(before["value_decimal"])
                    changes.append(
                        {
                            "from_period": before["period"],
                            "to_period": after["period"],
                            "delta": str(delta),
                            "direction": "上升" if delta > 0 else "下降" if delta < 0 else "持平",
                            "input_fact_ids": [before["fact_id"], after["fact_id"]],
                        }
                    )
        output.append(
            {
                "identity": json.loads(identity),
                "rows": ordered,
                "statistics": results,
                "changes": changes,
                "input_fact_ids": [v["fact_id"] for v in ordered],
                "operations": operations,
                "formula_version": "decimal-series-v1",
            }
        )
    return output


def summary(facts: list[dict]) -> dict:
    """给模型导航，不重复大量行；完整输入留在 SQL 产物。"""
    return {
        "verified_fact_count": len(facts),
        "available_operations": list(OPERATIONS),
        "next_step": "final.numeric_blocks 用当前 analysis_id；省略 fact_ids 表示该查询的全部原始数值。程序复核并渲染，勿在 answer/claims 抄数或描述数值趋势。",
    }


def _input(root: Path, row: dict, turn_id: str, request: NumericBlockRequest) -> list[dict]:
    with storage.open_db(root) as connection:
        analysis = storage.get_analysis_run(connection, str(request.analysis_id))
        receipts = storage.list_agent_evidence_receipts(connection, turn_id)
    if (
        analysis is None
        or analysis["session_id"] != str(row["id"])
        or analysis["ticker"] != row["ticker"]
        or analysis.get("turn_id") != turn_id
    ):
        raise AgentError("unknown_numeric_analysis", "数值产物不属于当前公司/Turn", exit_code=2)
    if analysis["status"] not in {"sql_succeeded", "function_succeeded"}:
        raise AgentError(
            "numeric_analysis_not_ready", "数值输入必须来自成功的 SQL 产物", exit_code=2
        )
    payload = json.loads(analysis["result_json"])
    facts = payload.get("sql", payload).get("verified_facts", [])
    if request.fact_ids:
        ids = set(request.fact_ids)
        facts = [item for item in facts if item["fact_id"] in ids]
        if {item["fact_id"] for item in facts} != ids:
            raise AgentError(
                "unknown_numeric_fact", "所选 fact_id 未出现在对应 SQL 的真实输入中", exit_code=2
            )
    if not facts:
        raise AgentError(
            "numeric_input_missing",
            "SQL 未取得带 fact_id 的原始数值，不能用目录或聚合别名冒充输入",
            exit_code=2,
        )
    seen = {(r["fact_id"], r["source_id"], r["locator"], r["source_sha256"]) for r in receipts}
    if any(
        (f["fact_id"], f["source_id"], f["locator"], f["source_sha256"]) not in seen for f in facts
    ):
        raise AgentError("missing_numeric_receipt", "数值输入缺少本 Turn 的事实回执", exit_code=2)
    return facts


def render(series: list[dict]) -> str:
    lines = []
    names = {"mean": "均值", "range": "极差", "population_stddev": "总体标准差"}
    for block in series:
        identity = block["identity"]
        unit = identity.get("unit")
        percent = unit in {"%", "percent", "percentage"}
        level_unit = "%" if percent else unit or "原始数值，单位未确认"
        delta_unit = "百分点" if percent else unit or "原始尺度，单位未确认"
        lines.extend(
            [
                f"\n**{identity.get('label') or identity.get('field_key')}**",
                f"单位：{level_unit}；倍率：{identity.get('scale') or '未确认'}；币种：{identity.get('currency') or '未确认'}。",
            ]
        )
        lines.extend(["", "| 期间 | 原始值 |", "| --- | ---: |"])
        lines.extend(
            f"| {r.get('period') or '期间未知'} | {r['value_decimal']} |" for r in block["rows"]
        )
        for name, value in block["statistics"].items():
            # 原始字符串保存在 JSON；只在显示时舍入。
            display = format(Decimal(value), ".6f").rstrip("0").rstrip(".")
            lines.append(
                f"\n{names[name]}：{display}（{delta_unit if name != 'mean' else level_unit}）。"
            )
        for change in block["changes"]:
            lines.append(
                f"\n{change['from_period']} → {change['to_period']}：{change['direction']}，差值 {change['delta']}（{delta_unit}）。"
            )
    return "\n".join(lines)


def prepare(
    root: Path, row: dict, turn_id: str, decision: AgentDecision, task: dict
) -> tuple[str, list[dict], list[EvidenceRef]]:
    series = []
    for request in decision.numeric_blocks:
        facts = _input(root, row, turn_id, request)
        attribute = task.get("value_attribute", "data")
        if any(
            f.get("field_key")
            and f["field_key"].rsplit(":", 1)[-1] in {"data", "yoy", "qoq"}
            and not f["field_key"].endswith(":" + attribute)
            for f in facts
        ):
            raise AgentError(
                "numeric_attribute_mismatch",
                "输入原值/同比/环比与问题口径不一致；保留准确 field_key",
                exit_code=2,
            )
        kind = task.get("period_kind")
        if kind and any(f.get("period_kind") and f["period_kind"] != kind for f in facts):
            raise AgentError(
                "numeric_period_kind_mismatch",
                "数值输入的年度/中期/季度类型与目标不一致",
                exit_code=2,
            )
        if (
            task.get("basis") == "calendar_year"
            and kind == "annual"
            and any(not str(f.get("period_end") or "").endswith("-12-31") for f in facts)
        ):
            raise AgentError(
                "numeric_coverage_needs_confirmation",
                "你确认的是完整自然年，但所选年度事实的期末不是12月31日或尚未确认；请确认具体公司财年范围，不能直接替代自然年。",
                exit_code=2,
            )
        series.extend(
            {**item, "analysis_id": str(request.analysis_id)}
            for item in calculate(facts, request.operations)
        )
    for block in series:
        facts = block["rows"]
        expected = set(task.get("target_years", []))
        selected_years = {
            int(m.group())
            for f in facts
            if (m := re.search(r"(?:19|20)\d{2}", str(f.get("period"))))
        }
        if expected and selected_years - expected:
            raise AgentError(
                "numeric_period_mismatch",
                "数值输入包含目标范围外年份；按已确认年份重新筛选，不能取最近若干条替代",
                exit_code=2,
            )
        missing = expected - selected_years
        expected_periods = set(task.get("target_periods", []))
        missing_periods = expected_periods - {f.get("period") for f in facts}
        if (missing or missing_periods) and not task.get("accept_partial"):
            raise AgentError(
                "numeric_coverage_needs_confirmation",
                f"{block['identity'].get('label')} 的目标期间缺少 {sorted(missing_periods) if missing_periods else sorted(missing)}；请确认保留目标并列缺口，还是调整范围/补取，不能默默替换",
                exit_code=2,
            )
        if (missing or missing_periods) and not decision.gaps:
            raise AgentError(
                "numeric_gap_missing", "已允许缺年，但最终报告必须说明缺口", exit_code=2
            )
        if set(task.get("required_operations", [])) - set(block["operations"]):
            raise AgentError(
                "numeric_operation_missing",
                "用户要求的计算尚未包含在 numeric_blocks.operations 中",
                exit_code=2,
            )
    if series:
        for terms in task.get("metric_terms", []):
            if (
                not any(
                    any(
                        term
                        in (
                            str(b["identity"].get("label", ""))
                            + " "
                            + str(b["identity"].get("field_key", ""))
                        ).casefold()
                        for term in terms
                    )
                    for b in series
                )
                and not decision.gaps
            ):
                raise AgentError(
                    "numeric_metric_missing",
                    "用户要求的指标未出现在数值区块中；继续定位或明确记录该指标缺口",
                    exit_code=2,
                )
    texts = [
        str(decision.answer),
        *[claim.text for claim in decision.claims],
        *decision.counterarguments,
    ]
    if series:
        # 数值/趋势放入确定性区块；自由正文不再维护第二份数值或趋势结论。
        for text in texts:
            cleaned = re.sub(r"(?:19|20)\d{2}(?:[/年-](?:FY|\d{1,2}))?", "", text)
            if re.search(
                r"\d|%|％|百分点|上升|下降|回升|下滑|增长|减少|趋稳|波动(?:加大|收窄)|连续.{0,4}(?:升|降)",
                cleaned,
            ):
                raise AgentError(
                    "numeric_text_not_allowed",
                    "数值、百分号和数值趋势只放 numeric_blocks；answer/claims 保留原因证据与限制，不重复抄写",
                    exit_code=2,
                )
    elif task and re.search(
        r"(?<!\d)\d+(?:\.\d+)?\s*(?:%|％|百分点)|均值(?:为|是)|标准差(?:为|是)|极差(?:为|是)",
        " ".join(texts),
    ):
        raise AgentError(
            "numeric_block_required",
            "数值计算结论须用 numeric_blocks 绑定成功 SQL 输入",
            exit_code=2,
        )
    if not series and task:
        with storage.open_db(root) as connection:
            analyses = storage.list_analysis_runs(connection, str(row["id"]), turn_id=turn_id)
            financial_receipts = bool(task.get("period_kind")) and any(
                r.get("fact_id") for r in storage.list_agent_evidence_receipts(connection, turn_id)
            )
        available = any(
            a.get("result_json")
            and json.loads(a["result_json"])
            .get("sql", json.loads(a["result_json"]))
            .get("verified_facts")
            for a in analyses
            if a["status"] in {"sql_succeeded", "function_succeeded"}
        )
        financial_text = re.sub(r"(?:19|20)\d{2}", "", " ".join(texts))
        if (available and task.get("required_operations")) or (
            (available or financial_receipts) and re.search(r"\d", financial_text)
        ):
            raise AgentError(
                "numeric_block_required",
                "已有原始数值输入；请用 numeric_blocks 完成所需通用运算，不能因临时函数失败而声称没有计算能力",
                exit_code=2,
            )
    refs = []
    for block in series:
        for fact in block["rows"]:
            ref = EvidenceRef(
                source_id=fact["source_id"], locator=fact["locator"], relation="supports"
            )
            if ref not in refs:
                refs.append(ref)
    return str(decision.answer) + render(series), series, refs
