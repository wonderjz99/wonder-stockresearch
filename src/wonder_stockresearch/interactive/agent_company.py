"""公司范围的 Turn 快照。来源授权在主库，company.db 只是可重建事实投影。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from wonder_stockresearch import company_data, storage
from wonder_stockresearch.company_parser import DATASETS, PARSER_VERSION, CompanyDataError
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import Source


class SourcePin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Source
    parser_version: str | None = None


class CompanyScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    sources: list[SourcePin]


def freeze(connection: sqlite3.Connection, ticker: str, run_id: str | None) -> dict[str, Any]:
    latest: dict[str, dict[str, Any]] = {}
    for item in sorted(
        storage.list_sources_for_ticker(connection, ticker),
        key=lambda item: (item["retrieved_at"], item["id"]),
    ):
        metadata = json.loads(item["metadata_json"])
        if metadata.get("provider") == "futu" and metadata.get("dataset") in DATASETS:
            latest[metadata["dataset"]] = item
    rows = storage.get_run_sources(connection, run_id) if run_id else []
    selected = {
        item["id"]: item
        for item in rows
        if json.loads(item["metadata_json"]).get("dataset") not in DATASETS
    }
    selected.update({item["id"]: item for item in latest.values()})
    pins = []
    for item in selected.values():
        source = Source.model_validate_json(item["source_json"])
        if source.metadata.get("dataset") in DATASETS:
            source, _ = company_data._identity(item)
        pins.append(
            SourcePin(
                source=source,
                parser_version=(
                    PARSER_VERSION if source.metadata.get("dataset") in DATASETS else None
                ),
            )
        )
    return CompanyScope(sources=pins).model_dump(mode="json")


def scope(connection: sqlite3.Connection, row: dict[str, Any]) -> CompanyScope | None:
    turn_id = row.get("turn_id")
    if not turn_id:
        return None
    turn = storage.get_agent_turn(connection, str(turn_id))
    if (
        turn is None
        or turn["session_id"] != str(row["id"])
        or turn["ticker"] != row["ticker"]
        or turn.get("run_id") != row.get("run_id")
    ):
        raise AgentError("unknown_agent_turn", "公司资料范围不属于当前 Turn", exit_code=2)
    try:
        payload = json.loads(turn["question_json"])
        if payload.get("context") != "company_latest":
            return None
        return CompanyScope.model_validate(payload["_company_scope"])
    except (KeyError, ValueError, ValidationError) as error:
        raise AgentError("invalid_company_scope", "公司来源版本快照损坏", exit_code=2) from error


def _pinned_rows(
    connection: sqlite3.Connection, row: dict[str, Any], selected: CompanyScope
) -> list[dict[str, Any]]:
    result = []
    for pin in selected.sources:
        current = storage.get_source(connection, str(pin.source.id))
        if (
            current is None
            or current["ticker"] != row["ticker"]
            or Source.model_validate_json(current["source_json"]) != pin.source
            or current["sha256"] != pin.source.sha256
            or current["raw_path"] != pin.source.raw_path
        ):
            raise AgentError("company_source_changed", "已固定的公司来源身份发生变化", exit_code=2)
        result.append(current)
    return result


def context_sources(
    connection: sqlite3.Connection, row: dict[str, Any], *, active: bool = True
) -> list[dict[str, Any]]:
    selected = scope(connection, row)
    if selected is None:
        return storage.agent_context_sources(
            connection, str(row["ticker"]), row.get("run_id"), str(row["id"]), row.get("turn_id")
        )
    pins = _pinned_rows(connection, row, selected)
    fetched = [
        item
        for item in storage.list_sources_for_ticker(connection, str(row["ticker"]))
        if storage.is_agent_turn_source(item, str(row["id"]), row.get("turn_id"))
    ]
    if not active:
        return list({item["id"]: item for item in [*pins, *fetched]}.values())
    latest = {}
    other = []
    for item in [*pins, *sorted(fetched, key=lambda item: (item["retrieved_at"], item["id"]))]:
        dataset = json.loads(item["metadata_json"]).get("dataset")
        if dataset in DATASETS:
            latest[dataset] = item
        else:
            other.append(item)
    return [*other, *latest.values()]


def allowed(connection: sqlite3.Connection, row: dict[str, Any], source: dict[str, Any]) -> bool:
    if source["ticker"] != row["ticker"]:
        return False
    if scope(connection, row) is not None:
        return source["id"] in {
            item["id"] for item in context_sources(connection, row, active=False)
        }
    return (
        not row.get("run_id")
        or storage.is_source_linked(connection, row["run_id"], source["id"])
        or storage.is_agent_turn_source(source, str(row["id"]), row.get("turn_id"))
    )


def verify_archive(root: Path, source: dict[str, Any]) -> None:
    model = Source.model_validate_json(source["source_json"])
    if (
        source["id"] != str(model.id)
        or source["ticker"] != model.ticker
        or source["sha256"] != model.sha256
        or source["raw_path"] != model.raw_path
        or json.loads(source["metadata_json"]) != model.metadata
    ):
        raise AgentError("company_source_changed", "公司来源登记与来源快照不一致", exit_code=2)
    try:
        company_data._archive(root, model)
    except CompanyDataError as error:
        raise AgentError(error.code, error.message, exit_code=2) from error


def facts(root: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    """仅投影选中的四类来源，查询匹配来源哈希与解析版本的批次。"""
    with storage.open_db(root) as connection:
        selected = scope(connection, row)
        if selected is None:
            return None
        sources = context_sources(connection, row)
    structured = [
        item for item in sources if json.loads(item["metadata_json"]).get("dataset") in DATASETS
    ]
    for item in structured:
        verify_archive(root, item)
    versions = {str(pin.source.id): pin.parser_version for pin in selected.sources}
    current_ids = [
        item["id"]
        for item in structured
        if versions.get(item["id"], PARSER_VERSION) == PARSER_VERSION
    ]
    if current_ids:
        try:
            company_data.sync(root, str(row["ticker"]), source_ids=current_ids)
        except CompanyDataError as error:
            raise AgentError(error.code, error.message, exit_code=2) from error
    result: dict[str, Any] = {"financial_facts": [], "company_profile_facts": [], "coverage": []}
    if not structured:
        result["coverage"] = [{"dataset": name, "status": "missing"} for name in DATASETS]
        return result
    try:
        with company_data._readonly(company_data.database_path(root)) as connection:
            company_data._check_version(connection)
            for item in structured:
                dataset = json.loads(item["metadata_json"])["dataset"]
                batch = connection.execute(
                    "SELECT * FROM source_batches WHERE source_id=? AND source_sha256=? AND parser_version=?",
                    (item["id"], item["sha256"], versions.get(item["id"], PARSER_VERSION)),
                ).fetchone()
                if batch is None:
                    raise AgentError(
                        "company_parser_snapshot_missing",
                        "固定版本的公司事实批次不存在",
                        exit_code=2,
                    )
                _source, identity = company_data._identity(item)
                if batch["source_identity_json"] != identity:
                    raise AgentError(
                        "company_projection_mismatch", "公司事实投影与来源身份不一致", exit_code=2
                    )
                result["coverage"].append(
                    {
                        "dataset": dataset,
                        "source_id": item["id"],
                        "status": batch["status"],
                        "parser_version": batch["parser_version"],
                        "diagnostics": json.loads(batch["diagnostics_json"])[:10],
                    }
                )
                if batch["status"] == "failed":
                    continue
                for table in ("financial_facts", "company_profile_facts"):
                    values = connection.execute(
                        f"SELECT * FROM {table} WHERE batch_id=? ORDER BY locator LIMIT 10001",
                        (batch["id"],),
                    ).fetchall()
                    for value in values:
                        fact = dict(value)
                        fact.pop("batch_id")
                        fact.update(
                            source_id=item["id"], source_sha256=item["sha256"], dataset=dataset
                        )
                        result[table].append(fact)
    except CompanyDataError as error:
        raise AgentError(error.code, error.message, exit_code=2) from error
    present = {item["dataset"] for item in result["coverage"]}
    result["coverage"].extend(
        {"dataset": name, "status": "missing"} for name in DATASETS if name not in present
    )
    return result


_SEARCH_ALIASES = (
    ("total assets", "总资产", "资产总额", "资产合计", "资产总计"),
    ("total liabilities", "总负债", "负债总额", "负债合计", "负债总计"),
    ("cash and cash equivalents", "现金及等价物", "现金及现金等价物", "现金余额", "现金资产"),
    ("revenue", "营业收入", "营收", "收入"),
    ("net profit", "净利润"),
    ("gross margin", "毛利率"),
    ("net margin", "净利率"),
    ("roe", "净资产收益率"),
    ("operating cash flow", "经营活动产生的现金流量", "经营现金"),
    ("capex", "资本支出", "购买固定资产", "购买无形资产", "购建"),
    ("headquarters", "总办事处", "总部"),
    ("company name", "公司名称"),
    ("business", "公司业务", "主营业务"),
)


def search_terms(query: str) -> set[str]:
    """仅扩展检索词，不合并科目，也不证明经济口径相同。"""
    normalized = query.casefold().strip()
    terms = set(normalized.split())
    for group in _SEARCH_ALIASES:
        if normalized in group:
            return set(group)
    for group in _SEARCH_ALIASES:
        if any(term in normalized for term in group):
            terms.update(group)
    return terms


def match_rank(item: dict, terms: set[str]) -> int:
    """别名只提高候选排序；同比、比例与原值仍保持独立身份。"""
    label = item["label"].casefold().strip()
    if label in terms:
        return 0
    if any(label.startswith(term) for term in terms):
        return 1
    return 2


def coverage_summary(data: dict, dataset: str | None = None) -> dict:
    rows = [f for f in data["financial_facts"] if dataset is None or f.get("dataset") == dataset]
    return {
        "available_period_kinds": sorted({f["period_kind"] for f in rows if f.get("period_kind")}),
        "earliest_period": min((str(f["period"]) for f in rows if f.get("period")), default=None),
        "latest_period": max((str(f["period"]) for f in rows if f.get("period")), default=None),
        "note": "仅描述本 Turn 授权归档的已解析覆盖；期末不证明披露时间，未取得不证明公司未披露。",
    }


def query(
    root: Path,
    row: dict[str, Any],
    *,
    dataset: str | None,
    query: str,
    period: str | None,
    mode: str,
    limit: int,
    periods: list[str] | None = None,
    period_kind: str | None = None,
    field_keys: list[str] | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    data = facts(root, row)
    if data is None:
        raise AgentError(
            "company_scope_required", "该工具需要 context=company_latest 的新问题", exit_code=2
        )
    terms = search_terms(query)
    coverage_info = coverage_summary(data, dataset)
    coverage_info["requested_period_kind"] = period_kind
    coverage_info["period_kind_unavailable"] = bool(
        period_kind and period_kind not in coverage_info["available_period_kinds"]
    )
    records = []
    catalog: dict[tuple, dict[str, Any]] = {}
    items = sorted(
        [*data["company_profile_facts"], *data["financial_facts"]],
        key=lambda item: (str(item.get("period", "")), item["field_key"], item["locator"]),
        reverse=True,
    )
    for item in items:
        if dataset and item["dataset"] != dataset:
            continue
        if period and item.get("period") != period:
            continue
        if periods and item.get("period") not in periods:
            continue
        if period_kind and item.get("period_kind") != period_kind:
            continue
        if field_keys and item["field_key"] not in field_keys:
            continue
        if terms and not any(
            term in (item["field_key"] + " " + item["label"]).casefold() for term in terms
        ):
            continue
        if mode == "facts":
            records.append(item)
        if mode in {"fields", "facts"}:
            keys = (
                "dataset",
                "field_key",
                "label",
                "period_kind",
                "unit",
                "currency",
                "scale",
                "dimensions_json",
            )
            # fiscal_year 属于期间，不应把每年重复成不同字段；其余真实业务维度仍区分。
            dimensions = json.loads(item.get("dimensions_json", "{}"))
            dimensions.pop("fiscal_year", None)
            record = {key: item.get(key) for key in keys if key != "dimensions_json"}
            record["dimensions_json"] = json.dumps(dimensions, ensure_ascii=False, sort_keys=True)
            identity = tuple(record[key] for key in keys)
            entry = catalog.setdefault(identity, {**record, "periods": [], "fact_count": 0})
            if item.get("period") is not None and item["period"] not in entry["periods"]:
                entry["periods"].append(item["period"])
            entry["fact_count"] += 1
    for record in catalog.values():
        periods_found = record["periods"]
        if mode == "fields" or len(periods_found) > 5:
            record["period_count"] = len(periods_found)
            record["latest_period"] = periods_found[0] if periods_found else None
            record["earliest_period"] = periods_found[-1] if periods_found else None
            record["periods_omitted"] = max(0, len(periods_found) - 5)
            record["periods"] = periods_found[:5]
    if mode == "fields":
        records = list(catalog.values())
    if terms:
        records.sort(
            key=lambda item: (match_rank(item, terms), item["field_key"].endswith((":yoy", ":qoq")))
        )
    catalog_records = sorted(
        catalog.values(),
        key=lambda item: (
            match_rank(item, terms) if terms else 0,
            item["field_key"].endswith((":yoy", ":qoq")),
        ),
    )
    coverage = [item for item in data["coverage"] if dataset is None or item["dataset"] == dataset]
    navigation = (
        {}
        if mode == "fields"
        else {
            "field_catalog": catalog_records[:8],
            "field_catalog_omitted": max(0, len(catalog) - 8),
        }
    )
    page = records[offset : offset + limit]
    has_more = offset + len(page) < len(records)
    return {
        "scope": "company_latest",
        "coverage": coverage,
        "coverage_diagnostic": coverage_info,
        "mode": mode,
        **navigation,
        "records": page,
        "total_matches": len(records),
        "returned_records": len(page),
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + len(page) if has_more else None,
        "filters": {
            "dataset": dataset,
            "query": query,
            "period": period,
            "periods": periods or [],
            "period_kind": period_kind,
            "field_keys": field_keys or [],
        },
        "sql_tables": {
            "financial_facts": [
                "fact_id",
                "field_key",
                "label",
                "value_decimal",
                "period",
                "period_kind",
                "dataset",
                "unit",
                "currency",
                "scale",
                "dimensions_json",
            ],
            "company_profile_facts": [
                "fact_id",
                "field_key",
                "label",
                "value_json",
                "value_type",
                "dataset",
            ],
        },
        "next_step": "字段目录没有数值；用 run_readonly_sql 按 field_key、期间和口径选择最小输入，保留 fact_id。"
        if mode == "fields"
        else "按相同筛选条件和 next_offset 继续 query_company_data，或按 field_keys/periods/period_kind 缩小查询；省略不等于缺失，同义词命中不证明口径相同。",
    }
