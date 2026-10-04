"""阶段 18.1 的来源优先级、提取方式与可复现数值核对。

获取顺序和引用权威性是两件事：富途结构化数据适合先提供计算输入，正式
申报、交易所公告和公司官网在出现冲突时优先。搜索摘要只可发现原文，不能
成为 supports 证据。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any

from .models import Source


class SourceRank(StrEnum):
    OFFICIAL = "official"
    FUTU_STRUCTURED = "futu_structured"
    QUALIFIED_SECONDARY = "qualified_secondary"
    SEARCH_DISCOVERY = "search_discovery"


class ExtractionMethod(StrEnum):
    NATIVE_STRUCTURED = "native_structured"
    NATIVE_HTML = "native_html"
    NATIVE_PDF_TEXT = "native_pdf_text"
    OCR_DERIVED = "ocr_derived"
    SEARCH_DISCOVERY = "search_discovery"
    UNKNOWN = "unknown"


class ReconciliationStatus(StrEnum):
    MATCHED = "matched"
    PROVIDER_UNVERIFIED = "provider_unverified"
    CONFLICT = "conflict"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class SourceConflict:
    id: str
    provider_source_id: str
    official_source_id: str | None
    metric: str
    period: str | None
    currency: str | None
    provider_value: str | None
    official_value: str | None
    status: ReconciliationStatus
    details: dict[str, Any]

    def to_storage_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider_source_id": self.provider_source_id,
            "official_source_id": self.official_source_id,
            "metric": self.metric,
            "period": self.period,
            "currency": self.currency,
            "provider_value": self.provider_value,
            "official_value": self.official_value,
            "status": self.status.value,
            "details_json": json.dumps(self.details, ensure_ascii=False, sort_keys=True),
        }


_RANK_ORDER = {
    SourceRank.OFFICIAL: 0,
    SourceRank.FUTU_STRUCTURED: 1,
    SourceRank.QUALIFIED_SECONDARY: 2,
    SourceRank.SEARCH_DISCOVERY: 3,
}

_STRUCTURE_ORDER = {
    ExtractionMethod.NATIVE_STRUCTURED: 0,
    ExtractionMethod.NATIVE_HTML: 1,
    ExtractionMethod.NATIVE_PDF_TEXT: 2,
    ExtractionMethod.OCR_DERIVED: 3,
    ExtractionMethod.SEARCH_DISCOVERY: 4,
    ExtractionMethod.UNKNOWN: 5,
}


def default_rank(kind: str, metadata: dict[str, Any]) -> SourceRank:
    raw = metadata.get("source_rank")
    try:
        return SourceRank(str(raw))
    except (TypeError, ValueError):
        if metadata.get("search_discovery") is True:
            return SourceRank.SEARCH_DISCOVERY
        if kind == "primary":
            return SourceRank.OFFICIAL
        if kind == "provider" and str(metadata.get("provider") or "").casefold() == "futu":
            return SourceRank.FUTU_STRUCTURED
        return SourceRank.QUALIFIED_SECONDARY


def default_extraction_method(
    metadata: dict[str, Any], *, extension: str | None = None, extraction_status: str | None = None
) -> ExtractionMethod:
    raw = metadata.get("extraction_method")
    try:
        return ExtractionMethod(str(raw))
    except (TypeError, ValueError):
        if default_rank("", metadata) is SourceRank.SEARCH_DISCOVERY:
            return ExtractionMethod.SEARCH_DISCOVERY
        if metadata.get("dataset") or metadata.get("provider") == "futu":
            return ExtractionMethod.NATIVE_STRUCTURED
        if metadata.get("ocr_engine"):
            return ExtractionMethod.OCR_DERIVED
        if extension == "html":
            return ExtractionMethod.NATIVE_HTML
        if extension == "pdf" and extraction_status == "available":
            return ExtractionMethod.NATIVE_PDF_TEXT
        return ExtractionMethod.UNKNOWN


def normalized_metadata(
    kind: str,
    metadata: dict[str, Any],
    *,
    extension: str | None = None,
    extraction_status: str | None = None,
) -> dict[str, Any]:
    """填充稳定来源标签；用户显式提供的合法标签仍会被保留。"""
    result = dict(metadata)
    rank = default_rank(kind, result)
    method = default_extraction_method(result, extension=extension, extraction_status=extraction_status)
    result["source_rank"] = rank.value
    result["extraction_method"] = method.value
    if rank is SourceRank.SEARCH_DISCOVERY:
        result["supports_eligible"] = False
    else:
        result.setdefault("supports_eligible", True)
    if rank is SourceRank.FUTU_STRUCTURED and result.get("evidence_class") == "reported":
        result.setdefault("reconciliation_status", ReconciliationStatus.PROVIDER_UNVERIFIED.value)
    else:
        result.setdefault("reconciliation_status", ReconciliationStatus.NOT_APPLICABLE.value)
    return result


def rank_for(source: Source) -> SourceRank:
    return default_rank(source.kind, source.metadata)


def extraction_for(source: Source) -> ExtractionMethod:
    return default_extraction_method(source.metadata, extraction_status=source.extraction_status)


def reconciliation_for(source: Source) -> ReconciliationStatus:
    try:
        return ReconciliationStatus(str(source.metadata.get("reconciliation_status")))
    except (TypeError, ValueError):
        return ReconciliationStatus.NOT_APPLICABLE


def supports_eligible(source: Source) -> bool:
    return rank_for(source) is not SourceRank.SEARCH_DISCOVERY and bool(
        source.metadata.get("supports_eligible", True)
    )


def ranking_key(
    *,
    usable: bool,
    question_match: int,
    source: Source,
    period_match: int,
    locator: str,
) -> tuple[int, int, int, int, int, str, str]:
    """证据包的稳定排序：可用性、题意、权威、结构化、期间、确定性定位。"""
    return (
        0 if usable else 1,
        -question_match,
        _RANK_ORDER[rank_for(source)],
        _STRUCTURE_ORDER[extraction_for(source)],
        -period_match,
        str(source.id),
        locator,
    )


def _canonical_metric(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).casefold().split())
    return text or None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("%", ""))
    except (InvalidOperation, ValueError):
        return None


def _provider_nodes(value: Any, inherited: dict[str, Any] | None = None) -> Iterable[dict[str, Any]]:
    inherited = inherited or {}
    if isinstance(value, dict):
        context = dict(inherited)
        for key in ("period_text", "period", "date_time_str", "currency_code", "currency", "unit"):
            if value.get(key) is not None:
                context[key] = value[key]
        if "item_list" in value and isinstance(value["item_list"], list):
            for item in value["item_list"]:
                if isinstance(item, dict):
                    yield {**context, **item}
        if ("data" in value or "value" in value) and (value.get("display_name") or value.get("name")):
            yield {**context, **value}
        for child in value.values():
            yield from _provider_nodes(child, context)
    elif isinstance(value, list):
        for child in value:
            yield from _provider_nodes(child, inherited)


def _source_payload(root: Path, source: Source) -> object | None:
    path = root / source.raw_path
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _provider_facts(root: Path, source: Source) -> list[dict[str, str | None]]:
    payload = _source_payload(root, source)
    if payload is None:
        return []
    facts: list[dict[str, str | None]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for item in _provider_nodes(payload):
        metric = _canonical_metric(item.get("field_id") or item.get("display_name") or item.get("name"))
        value = _decimal(item.get("data", item.get("value")))
        period = item.get("period_text") or item.get("period") or item.get("date_time_str")
        if metric is None or value is None or period is None:
            continue
        currency = item.get("currency_code") or item.get("currency")
        key = (metric, str(period), str(currency) if currency else None)
        if key in seen:
            continue
        seen.add(key)
        facts.append(
            {
                "metric": metric,
                "period": str(period),
                "currency": str(currency) if currency else None,
                "value": str(value),
            }
        )
    return facts


def _official_facts(source: Source) -> list[dict[str, str | None]]:
    """官方结构化网页/人工导入可显式提供可核对字段；PDF 文本不猜测表格数值。"""
    raw = source.metadata.get("financial_facts")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str | None]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        metric = _canonical_metric(item.get("field_id") or item.get("metric") or item.get("name"))
        value = _decimal(item.get("value"))
        period = item.get("period")
        if metric is None or value is None or period is None:
            continue
        currency = item.get("currency")
        result.append(
            {
                "metric": metric,
                "period": str(period),
                "currency": str(currency) if currency else None,
                "value": str(value),
            }
        )
    return result


def reconcile_sources(root: Path, sources: list[Source]) -> list[SourceConflict]:
    official = [source for source in sources if rank_for(source) is SourceRank.OFFICIAL]
    official_facts = [
        (source, fact)
        for source in official
        for fact in _official_facts(source)
    ]
    conflicts: list[SourceConflict] = []
    for provider in sources:
        if rank_for(provider) is not SourceRank.FUTU_STRUCTURED:
            continue
        for fact in _provider_facts(root, provider):
            candidates = [
                (source, official_fact)
                for source, official_fact in official_facts
                if official_fact["metric"] == fact["metric"]
                and official_fact["period"] == fact["period"]
                and (not official_fact["currency"] or not fact["currency"] or official_fact["currency"] == fact["currency"])
            ]
            selected = candidates[0] if candidates else None
            status = ReconciliationStatus.PROVIDER_UNVERIFIED
            official_source_id: str | None = None
            official_value: str | None = None
            if selected is not None:
                source, official_fact = selected
                official_source_id = str(source.id)
                official_value = official_fact["value"]
                status = (
                    ReconciliationStatus.MATCHED
                    if Decimal(str(fact["value"])) == Decimal(str(official_value))
                    else ReconciliationStatus.CONFLICT
                )
            identity = "|".join(
                [str(provider.id), fact["metric"] or "", fact["period"] or "", official_source_id or ""]
            )
            conflicts.append(
                SourceConflict(
                    id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                    provider_source_id=str(provider.id),
                    official_source_id=official_source_id,
                    metric=str(fact["metric"]),
                    period=fact["period"],
                    currency=fact["currency"],
                    provider_value=fact["value"],
                    official_value=official_value,
                    status=status,
                    details={"official_detail_available": selected is not None},
                )
            )
    return conflicts
