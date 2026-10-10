"""离线解析公司归档；保留原始字段和定位，不推导财务口径或问答公式。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DATASETS = ("profile", "statements", "operational_efficiency", "revenue_breakdown")
PARSER_VERSION = "futu-company-v1"


class CompanyDataError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        self.exit_code = 2
        super().__init__(message)


class ProfileFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_key: str
    label: str
    value_json: str
    value_type: str
    effective_at: str | None = None
    locator: str


class FinancialFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_key: str
    label: str
    value_text: str
    value_decimal: str
    period: str | None
    period_kind: Literal["annual", "interim", "quarter", "unknown"]
    period_start: str | None = None
    period_end: str | None = None
    currency: str | None = None
    unit: str | None = None
    scale: str | None = None
    accounting_standard: str | None = None
    accounting_scope: str | None = None
    published_at: str | None = None
    dimensions_json: str
    locator: str


class ParsedCompanyData(BaseModel):
    profiles: list[ProfileFact] = Field(default_factory=list)
    financials: list[FinancialFact] = Field(default_factory=list)
    diagnostics: list[dict[str, str]] = Field(default_factory=list)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _text(value: Any) -> str | None:
    return str(value) if value is not None and value != "" else None


def _date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _kind(record: dict[str, Any]) -> str:
    label = str(record.get("period_text") or record.get("period") or "").rsplit("/", 1)[-1]
    kind = str(record.get("financial_type") or label).upper().replace("F10TYPE_", "")
    if kind in {"FY", "ANNUAL", "7"}:
        return "annual"
    if kind in {"H1", "Q6", "H2", "Q9", "5", "6"}:
        return "interim"
    if kind in {"Q1", "Q2", "Q3", "Q4", "1", "2", "3", "4"}:
        return "quarter"
    return "unknown"


def _list(value: Any, locator: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CompanyDataError("company_structure_unsupported", f"未覆盖的资料结构：{locator}")
    return value


def _number(
    result: ParsedCompanyData, *, key: str, label: str, raw: Any,
    record: dict[str, Any], locator: str, dimensions: dict[str, Any],
    currency: str | None = None, unit: str | None = None,
) -> None:
    if raw is None:
        code = "missing_numeric_value"
    else:
        try:
            if isinstance(raw, (bool, dict, list)):
                raise TypeError
            text = str(raw)
            value = Decimal(text.replace(",", "").removesuffix("%"))
            if not value.is_finite():
                raise ValueError
        except (InvalidOperation, ValueError, TypeError):
            code = "invalid_numeric_value"
        else:
            result.financials.append(FinancialFact(
                field_key=key, label=label, value_text=text, value_decimal=str(value),
                period=_text(record.get("period_text") or record.get("period")),
                period_kind=_kind(record),
                period_start=_date(record.get("start_date_str")),
                period_end=_date(record.get("end_date_str") or record.get("date_time_str")),
                currency=currency,
                unit="%" if text.endswith("%") else unit,
                scale=_text(record.get("scale")),
                accounting_standard=_text(record.get("accounting_standards")),
                accounting_scope=_text(record.get("accounting_scope")),
                # available_from 在旧采集器中可能只是报告期末，不能当作披露日。
                published_at=_text(record.get("published_at")),
                dimensions_json=_json(dimensions), locator=locator,
            ))
            return
    result.diagnostics.append({"code": code, "locator": locator})


def _profiles(payload: Any, result: ParsedCompanyData) -> None:
    for i, item in enumerate(_list(payload, "$.payload")):
        label = _text(item.get("name"))
        if label is None or "value" not in item:
            raise CompanyDataError("company_structure_unsupported", "基本资料缺少 name/value")
        value = item["value"]
        result.profiles.append(ProfileFact(
            field_key=label, label=label, value_json=_json(value),
            value_type=type(value).__name__, locator=f"$.payload[{i}].value",
            effective_at=_text(item.get("effective_at")),
        ))


def _statements(payload: dict[str, Any], result: ParsedCompanyData) -> None:
    statements = payload.get("statements")
    if not isinstance(statements, dict):
        raise CompanyDataError("company_structure_unsupported", "报表缺少 statements 对象")
    for statement_id, statement in statements.items():
        if not isinstance(statement, dict):
            raise CompanyDataError("company_structure_unsupported", "报表类型不是对象")
        # 使用 bracket JSONPath，保留数字类型键的精确位置。
        base = f"$.payload.statements[{json.dumps(statement_id)}]"
        names = {
            str(item.get("field_id")): item.get("display_name")
            for item in _list(statement.get("structure_list", []), base + ".structure_list")
        }
        for i, record in enumerate(_list(statement.get("report_list"), base + ".report_list")):
            section = None
            for j, item in enumerate(_list(record.get("item_list"), base + ".item_list")):
                label = _text(item.get("display_name") or names.get(str(item.get("field_id"))))
                if label is None:
                    raise CompanyDataError("company_structure_unsupported", "报表科目缺少名称")
                if "data" not in item:
                    section = label
                    continue
                field_id = str(item.get("field_id") or label)
                dimensions = {
                    "statement_type": statement_id, "statement_label": statement.get("label"),
                    "section": section, "reported_currency": record.get("currency_code"),
                    "fiscal_year": record.get("fiscal_year"),
                }
                for attribute in ("data", "yoy", "qoq"):
                    if attribute not in item:
                        continue
                    # 关键指标混有百分数/倍数/每股数；不能继承整个报告的币种给所有指标。
                    currency = _text(item.get("currency_code"))
                    if statement_id in {"1", "2", "3"} and attribute == "data":
                        currency = currency or _text(record.get("currency_code"))
                    combined = {**record, **item}
                    _number(
                        result, key=f"statements:{statement_id}:{field_id}:{attribute}",
                        label=label if attribute == "data" else f"{label} / {attribute}",
                        raw=item[attribute], record=combined,
                        locator=f"{base}.report_list[{i}].item_list[{j}].{attribute}",
                        dimensions={**dimensions, "attribute": attribute},
                        currency=currency if attribute == "data" else None,
                        unit=_text(item.get("unit")) if attribute == "data" else None,
                    )


def _efficiency(payload: dict[str, Any], result: ParsedCompanyData) -> None:
    metadata_keys = {
        "fiscal_year", "financial_type", "period_text", "period", "end_date", "end_date_str",
        "start_date_str", "published_at", "currency_code", "accounting_standards",
        "accounting_scope", "unit", "scale",
    }
    for i, item in enumerate(_list(payload.get("item_list"), "$.payload.item_list")):
        for key, raw in item.items():
            if key in metadata_keys:
                continue
            _number(
                result, key=f"operational_efficiency:{key}", label=key, raw=raw,
                record=item, locator=f"$.payload.item_list[{i}].{key}",
                dimensions={"fiscal_year": item.get("fiscal_year")},
                # 原始业务键决定货币字段，不给员工数或同比赋币种。
                currency=_text(payload.get("currency_code"))
                if key.endswith("_per_capita") else None,
            )


def _breakdown(payload: dict[str, Any], result: ParsedCompanyData) -> None:
    periods = [("$.payload", payload)]
    historical = _list(payload.get("historical_breakdowns", []), "$.payload.historical_breakdowns")
    periods.extend((f"$.payload.historical_breakdowns[{i}]", p) for i, p in enumerate(historical))
    seen: dict[tuple[str, str], list[list[dict[str, Any]]]] = {}
    for base, record in periods:
        groups = _list(record.get("breakdown_list"), base + ".breakdown_list")
        identity = (str(record.get("period")), str(record.get("currency_code")))
        # latest 在 historical 中常重复：完全一致才去重，冲突则保留两组定位。
        if groups in seen.get(identity, []):
            continue
        seen.setdefault(identity, []).append(groups)
        for i, group in enumerate(groups):
            for j, item in enumerate(_list(group.get("item_list"), base + ".item_list")):
                label = _text(item.get("name"))
                if label is None:
                    raise CompanyDataError("company_structure_unsupported", "营收构成缺少 name")
                for key, raw in item.items():
                    if key in {"name", "unit", "currency_code", "scale"}:
                        continue
                    _number(
                        result, key=f"revenue_breakdown:{key}", label=key, raw=raw,
                        record={**record, **item},
                        locator=f"{base}.breakdown_list[{i}].item_list[{j}].{key}",
                        dimensions={"category": group.get("type"), "segment": label},
                        currency=_text(item.get("currency_code") or record.get("currency_code"))
                        if key != "ratio" else None,
                        unit=_text(item.get("unit")),
                    )
    for failure in _list(payload.get("history_failures", []), "$.payload.history_failures"):
        result.diagnostics.append({
            "code": "archived_history_fetch_failed", "locator": "$.payload.history_failures",
            "period": str(failure.get("period") or "unknown"),
        })


def parse_company_archive(raw: bytes, dataset: str) -> ParsedCompanyData:
    """只支持本项目归档的 Futu envelope；不把未知形状误当成空数据。"""
    try:
        envelope = json.loads(raw, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompanyDataError("company_invalid_json", "公司来源不是有效 UTF-8 JSON") from error
    if not isinstance(envelope, dict) or envelope.get("provider") != "futu":
        raise CompanyDataError("company_provider_unsupported", "公司库首版仅解析 Futu 归档")
    if envelope.get("dataset") != dataset:
        raise CompanyDataError("company_dataset_mismatch", "来源登记与归档 dataset 不一致")
    payload = envelope.get("payload")
    result = ParsedCompanyData()
    if dataset == "profile":
        _profiles(payload, result)
    elif dataset in DATASETS and isinstance(payload, dict):
        {"statements": _statements, "operational_efficiency": _efficiency,
         "revenue_breakdown": _breakdown}[dataset](payload, result)
    else:
        raise CompanyDataError("company_structure_unsupported", "未覆盖的公司数据结构")
    return result
