"""正式研究与开放问答共享的归档财务输入和确定性计算。

保留 Decimal、期间/币种匹配、披露 Capex 与现金流代理口径以及证据定位。
这里不依赖固定报告流程、Agent、SQLite 或模型调用，也不决定公司评分。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from statistics import median, pstdev
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import EvidenceRef, Source, source_id_text

CALCULATION_VERSION = "fixed-calculations-v3"


class InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalculationInput(InternalModel):
    label: str
    value: Decimal
    period: str
    currency: str | None = None
    unit: str | None = None
    source_id: str
    locator: str


class CalculationResult(InternalModel):
    metric: str
    formula: str
    version: str = CALCULATION_VERSION
    values: list[dict[str, Any]]
    inputs: list[CalculationInput]
    source_refs: list[EvidenceRef]
    missing_periods: list[str] = Field(default_factory=list)
    incomparable_reasons: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


def source_text(root: Path, source: Source) -> str:
    path_value = source.text_path or source.raw_path
    path = root / path_value
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


_PROVIDER_CONTEXT_FIELDS = (
    "period_text",
    "period",
    "date_time_str",
    "fiscal_year",
    "financial_type",
    "currency",
    "currency_code",
    "currency_info",
    "unit",
    "accounting_standard",
    "accounting_standards",
)


def provider_nodes(
    value: Any, path: str = "$", inherited: dict[str, Any] | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """展开 provider JSON，并把 Futu report_list 的报告期元数据下沉给 item_list。"""
    found: list[tuple[str, dict[str, Any]]] = []
    inherited = inherited or {}
    if isinstance(value, dict):
        context = {
            **inherited,
            **{key: value[key] for key in _PROVIDER_CONTEXT_FIELDS if value.get(key) is not None},
        }
        if any(
            key in value
            for key in (
                "display_name",
                "name",
                "period",
                "period_text",
                "date_time_str",
                "display_leader_name",
                "leader_name",
                "position_name",
                "holder_pct",
                "publ_date",
                "pub_date",
                "buy_back_money",
                "statement",
            )
        ):
            merged = dict(value)
            for key, item in context.items():
                merged.setdefault(key, item)
            found.append((path, merged))
        # 富途 financial statements 的 item_list 只给 field_id；将同一响应的
        # structure_list.display_name 下沉，才能让固定计算和问题检索按真实科目工作。
        field_names = {
            str(item.get("field_id")): item.get("display_name")
            for item in value.get("structure_list", [])
            if isinstance(item, dict)
            and item.get("field_id") is not None
            and item.get("display_name")
        }
        if field_names and isinstance(value.get("report_list"), list):
            for index, report in enumerate(value["report_list"]):
                if not isinstance(report, dict):
                    continue
                enriched = dict(report)
                raw_items = report.get("item_list")
                if isinstance(raw_items, list):
                    enriched["item_list"] = [
                        {
                            **item,
                            "display_name": item.get("display_name")
                            or field_names.get(str(item.get("field_id"))),
                        }
                        if isinstance(item, dict)
                        else item
                        for item in raw_items
                    ]
                found.extend(provider_nodes(enriched, f"{path}.report_list[{index}]", context))
        for key, child in value.items():
            if key == "report_list" and field_names:
                continue
            found.extend(provider_nodes(child, f"{path}.{key}", context))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(provider_nodes(child, f"{path}[{index}]", inherited))
    return found


def numeric_inputs(sources: list[Source], root: Path) -> list[CalculationInput]:
    inputs: list[CalculationInput] = []
    for source in sources:
        if source.kind != "provider" and not source.metadata.get("dataset"):
            continue
        try:
            payload = json.loads(source_text(root, source))
        except (json.JSONDecodeError, TypeError):
            continue
        for locator, node in provider_nodes(payload):
            label = node.get("display_name") or node.get("name")
            raw = node.get("data", node.get("value"))
            period = node.get("period_text") or node.get("period") or node.get("date_time_str")
            if not label or raw is None or period is None or isinstance(raw, bool):
                continue
            try:
                value = Decimal(str(raw).replace(",", "").replace("%", ""))
            except (InvalidOperation, ValueError):
                continue
            label_text = str(label)
            normalized_label = label_text.lower()
            is_rate_or_ratio = (
                "roe" in normalized_label
                or "净资产收益率" in label_text
                or "ratio" in normalized_label
                or "比率" in label_text
            )
            inputs.append(
                CalculationInput(
                    label=label_text,
                    value=value,
                    period=str(period),
                    currency=str(node.get("currency") or node.get("currency_code"))
                    if not is_rate_or_ratio and (node.get("currency") or node.get("currency_code"))
                    else None,
                    unit=(
                        "%"
                        if "roe" in normalized_label or "净资产收益率" in label_text
                        else str(node.get("unit"))
                    )
                    if (
                        "roe" in normalized_label
                        or "净资产收益率" in label_text
                        or node.get("unit")
                    )
                    else None,
                    source_id=source_id_text(source.id),
                    locator=locator,
                )
            )
    return inputs


_ANNUAL_REPORT_YEAR = re.compile(r"\b(20\d{2})\b")
_CAPEX_DISCLOSURE = re.compile(
    r"capital\s+expenditure(?:\s+for\s+the\s+company\s+in\s+20\d{2}|\s+for\s+the\s+year)?\s+"
    r"(?:totalled|totaled|was|amounted to|reached)"
    r"(?:\s+approximately)?\s+(?P<currency>RMB|HK\$|US\$)\s*"
    r"(?P<amount>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<scale>billion|million)",
    re.IGNORECASE,
)
_CAPEX_TABLE_DISCLOSURE = re.compile(
    r"capital expenditure\s+(?P<currency>RMB|HK\$|US\$)\s*"
    r"(?P<amount>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<scale>mil|million|billion)",
    re.IGNORECASE,
)


def annual_capex_inputs(root: Path, sources: list[Source]) -> list[CalculationInput]:
    """抽取年报中明确披露的年度 Capex；不接受计划、指引或无币种的模糊句子。"""
    values: list[CalculationInput] = []
    currency_map = {"RMB": "CNY", "HK$": "HKD", "US$": "USD"}
    scale_map = {
        "billion": Decimal(1_000_000_000),
        "million": Decimal(1_000_000),
        "mil": Decimal(1_000_000),
    }
    for source in sources:
        if source.kind != "primary" or source.metadata.get("role") != "annual":
            continue
        years = _ANNUAL_REPORT_YEAR.findall(source.title)
        text = source_text(root, source)
        if len(years) != 1 or not text:
            continue
        period = f"{years[0]}/FY"
        # 年报摘要中的明确叙述优先于版面图表。两者通常重复同一数值；若同一优先级
        # 出现不同数值，便保守地不生成计算输入。
        matches: list[tuple[int, int, Decimal, str, str]] = []
        for page_number, page in enumerate(text.split("\f"), 1):
            for pattern in (_CAPEX_DISCLOSURE, _CAPEX_TABLE_DISCLOSURE):
                for match in pattern.finditer(page):
                    before = page[max(0, match.start() - 100) : match.start()].lower()
                    if any(token in before for token in ("expect", "guidance", "预计", "预期")):
                        continue
                    raw_amount = Decimal(match.group("amount").replace(",", ""))
                    amount = raw_amount * scale_map[match.group("scale").lower()]
                    currency = currency_map[match.group("currency").upper()]
                    normalized_label = " ".join(match.group(0).lower().split())
                    # “for the year” 是管理层对当年 Capex 的完整披露，通常给出较精确的
                    # 百万元金额；摘要中的四舍五入十亿元数字次之，图表最后。
                    priority = (
                        0
                        if "for the year" in normalized_label
                        else 1
                        if pattern is _CAPEX_DISCLOSURE
                        else 2
                    )
                    precision = len(match.group("amount").partition(".")[2])
                    matches.append((priority, precision, amount, currency, f"page {page_number}"))
        if not matches:
            continue
        priority = min(item[0] for item in matches)
        preferred = [item for item in matches if item[0] == priority]
        unique_values = {(amount, currency) for _, _, amount, currency, _ in preferred}
        # 同一指标可能在摘要中按十亿元四舍五入、在财务回顾中按百万元列示。仅在误差不
        # 超过 0.5% 且币种一致时，把它视作同一披露并选择小数位更多的精确数字。
        if len(unique_values) > 1:
            currencies = {currency for _, currency in unique_values}
            amounts = [amount for amount, _ in unique_values]
            if (
                len(currencies) != 1
                or not min(amounts)
                or max(amounts) / min(amounts) - 1 > Decimal("0.005")
            ):
                continue
            max_precision = max(item[1] for item in preferred)
            preferred = [item for item in preferred if item[1] == max_precision]
            unique_values = {(amount, currency) for _, _, amount, currency, _ in preferred}
        if len(unique_values) != 1:
            continue
        amount, currency = next(iter(unique_values))
        locator = next(
            locator
            for _, _, candidate_amount, candidate_currency, locator in preferred
            if (candidate_amount, candidate_currency) == (amount, currency)
        )
        values.append(
            CalculationInput(
                label="reported capital expenditure",
                value=amount,
                period=period,
                currency=currency,
                unit="currency",
                source_id=source_id_text(source.id),
                locator=locator,
            )
        )
    return values


def _summary(values: list[Decimal]) -> dict[str, Any]:
    if not values:
        return {}
    return {
        "min": min(values),
        "max": max(values),
        "median": Decimal(str(median(values))),
        "population_stddev": Decimal(str(pstdev(values))) if len(values) > 1 else Decimal(0),
    }


def _refs(inputs: list[CalculationInput]) -> list[EvidenceRef]:
    unique: dict[tuple[str, str], EvidenceRef] = {}
    for item in inputs:
        ref = EvidenceRef(source_id=item.source_id, locator=item.locator, relation="supports")
        unique[(item.source_id, item.locator)] = ref
    return list(unique.values())


def _preferred_series(
    inputs: list[CalculationInput], labels: tuple[str, ...]
) -> list[CalculationInput]:
    """每期间选一个明确优先级的字段，避免利润表/指标表的同名项目重复进入公式。"""
    candidates: list[tuple[int, CalculationInput]] = []
    for item in inputs:
        label = item.label.lower()
        for rank, expected in enumerate(labels):
            expected = expected.lower()
            if label == expected or expected in label:
                candidates.append((rank, item))
                break
    selected: dict[str, tuple[int, CalculationInput]] = {}
    for rank, item in candidates:
        previous = selected.get(item.period)
        # locator 作最后的稳定排序，保证同输入总是选中同一个值。
        if previous is None or (rank, item.locator) < (previous[0], previous[1].locator):
            selected[item.period] = (rank, item)
    return [selected[period][1] for period in sorted(selected)]


def _cash_capex_proxy(inputs: list[CalculationInput]) -> CalculationResult | None:
    """以现金流量表的购置固定资产/无形资产做现金 Capex 代理，不冒充公司披露的 Capex。"""
    labels = (
        "购买固定资产",
        "购买物业、厂房及设备",
        "购买无形资产",
        "purchase fixed asset",
        "purchase intangible",
    )
    rows = [item for item in inputs if any(label in item.label.lower() for label in labels)]
    by_period: dict[str, list[CalculationInput]] = {}
    for item in rows:
        by_period.setdefault(item.period, []).append(item)
    values: list[dict[str, Any]] = []
    selected: list[CalculationInput] = []
    incomparable: list[str] = []
    for period, items in sorted(by_period.items()):
        currencies = {item.currency for item in items}
        if len(currencies) != 1:
            incomparable.append(f"{period}: 购置资产项目币种不一致")
            continue
        # 各项流出在现金流量表中通常为负数；输出绝对值并保留公式说明。
        values.append(
            {
                "period": period,
                "value": sum((abs(item.value) for item in items), Decimal(0)),
                "unit": "reported currency",
                "currency": next(iter(currencies)),
                "basis": "cash-flow proxy: purchases of fixed assets and intangible assets",
            }
        )
        selected.extend(items)
    if not selected:
        return None
    return CalculationResult(
        metric="cash_capex_proxy",
        formula="sum(abs(cash paid for purchases of fixed assets and intangible assets))",
        values=values,
        inputs=selected,
        source_refs=_refs(selected),
        incomparable_reasons=incomparable,
        summary=_summary([Decimal(str(item["value"])) for item in values]),
    )


def _cash_capex_to_revenue(
    cash_capex: CalculationResult, revenues: dict[str, CalculationInput]
) -> CalculationResult | None:
    """把现金购置资产代理值除以同币种收入，明确保留“代理口径”而不冒充披露 Capex。"""
    capex_by_period = {str(item["period"]): item for item in cash_capex.values}
    ratios: list[dict[str, Any]] = []
    inputs = list(cash_capex.inputs)
    incomparable: list[str] = []
    matched_revenues: list[CalculationInput] = []
    for period in sorted(set(capex_by_period) & set(revenues)):
        capex = capex_by_period[period]
        revenue = revenues[period]
        if revenue.value == 0 or capex.get("currency") != revenue.currency:
            incomparable.append(f"{period}: 收入与现金 Capex 代理值币种不一致或收入为零")
            continue
        ratios.append(
            {
                "period": period,
                "value": Decimal(str(capex["value"])) / revenue.value * 100,
                "unit": "%",
                "basis": "cash-capex proxy / revenue",
            }
        )
        matched_revenues.append(revenue)
    if not ratios:
        return None
    inputs.extend(matched_revenues)
    return CalculationResult(
        metric="cash_capex_proxy_to_revenue",
        formula="cash-flow purchases of fixed and intangible assets / revenue * 100",
        values=ratios,
        inputs=inputs,
        source_refs=_refs(inputs),
        missing_periods=sorted(set(revenues) ^ set(capex_by_period)),
        incomparable_reasons=incomparable,
        summary=_summary([Decimal(str(item["value"])) for item in ratios]),
    )


def calculate_trusted_metrics(root: Path, sources: list[Source]) -> list[CalculationResult]:
    all_inputs = [*numeric_inputs(sources, root), *annual_capex_inputs(root, sources)]
    results: list[CalculationResult] = []
    profits = _preferred_series(
        all_inputs, ("归属母公司净利润", "净利润", "profit attributable", "net profit")
    )
    growth_values: list[dict[str, Any]] = []
    for previous, current in pairwise(profits):
        if previous.value != 0 and previous.currency == current.currency:
            growth_values.append(
                {
                    "period": current.period,
                    "value": (current.value / previous.value - 1) * 100,
                    "unit": "%",
                }
            )
    if profits:
        vals = [Decimal(str(item["value"])) for item in growth_values]
        results.append(
            CalculationResult(
                metric="net_profit_yoy_growth",
                formula="(current/prior-1)*100",
                values=growth_values,
                inputs=profits,
                source_refs=_refs(profits),
                missing_periods=[] if growth_values else ["需要至少两个可比期间"],
                summary=_summary(vals),
            )
        )
    roes = _preferred_series(all_inputs, ("净资产收益率(roe)", "roe", "净资产收益率"))
    if roes:
        values = [
            {"period": item.period, "value": item.value, "unit": item.unit or "%"} for item in roes
        ]
        results.append(
            CalculationResult(
                metric="roe_series",
                formula="reported ROE series",
                values=values,
                inputs=roes,
                source_refs=_refs(roes),
                summary={
                    **_summary([i.value for i in roes]),
                    "covered_years": len(roes),
                    "years_above_15_percent": sum(i.value > 15 for i in roes),
                },
            )
        )
    revenues = {
        item.period: item
        for item in _preferred_series(all_inputs, ("营业总收入", "营业收入", "revenue"))
    }
    capexes = {
        item.period: item
        for item in _preferred_series(
            all_inputs, ("reported capital expenditure", "资本开支", "资本支出", "capex")
        )
    }
    ratio_inputs: list[CalculationInput] = []
    ratios: list[dict[str, Any]] = []
    incomparable: list[str] = []
    for period in sorted(set(revenues) & set(capexes)):
        revenue, capex = revenues[period], capexes[period]
        if revenue.value == 0 or revenue.currency != capex.currency:
            incomparable.append(f"{period}: 收入与资本开支币种不一致或收入为零")
            continue
        ratios.append({"period": period, "value": capex.value / revenue.value * 100, "unit": "%"})
        ratio_inputs.extend((capex, revenue))
    if revenues or capexes:
        missing = sorted(set(revenues) ^ set(capexes))
        results.append(
            CalculationResult(
                metric="capex_to_revenue",
                formula="capex/revenue*100",
                values=ratios,
                inputs=ratio_inputs,
                source_refs=_refs(ratio_inputs),
                missing_periods=missing,
                incomparable_reasons=incomparable,
                summary=_summary([Decimal(str(i["value"])) for i in ratios]),
            )
        )
    cash_capex = _cash_capex_proxy(all_inputs)
    if cash_capex is not None:
        results.append(cash_capex)
        cash_ratio = _cash_capex_to_revenue(cash_capex, revenues)
        if cash_ratio is not None:
            results.append(cash_ratio)
    return results
