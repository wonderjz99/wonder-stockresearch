"""阶段 7.1：从富途只读接口获取公司基本面变量。

只使用 OpenQuoteContext（行情上下文），不导入、不调用任何账户、持仓、资金、
交易或自选分组接口。futu-api 惰性 import，离线导入本模块不连接服务。

每个数据集返回统一结构，便于归档与 as_of 判定：
    {"dataset", "evidence_class", "available_from", "currency",
     "accounting_standard", "payload"}

- `available_from`：能确定该数据“最晚到何时已可用”的日期（报告期末/披露日），
  有值时按它做 as_of 可用性判断；没有值时按快照规则（observed_at）。
- evidence_class：reported 表示有明确期间/披露日的事实；snapshot 表示时点快照。
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
from datetime import UTC, datetime
from typing import Any, Protocol

from .market import (
    DEFAULT_FUTU_HOST,
    DEFAULT_FUTU_PORT,
    OPEND_PROBE_TIMEOUT_SECONDS,
    _json_safe,
    _records,
)
from .sources import SourceError

FINANCIAL_RECORD_LIMIT = 10
REVENUE_ANNUAL_PERIOD_LIMIT = 10
REVENUE_INTERIM_PERIOD_LIMIT = 3
BUYBACK_RECORD_LIMIT = 20
DATASETS = (
    "profile",
    "executives",
    "operational_efficiency",
    "statements",
    "revenue_breakdown",
    "dividends",
    "buybacks",
    "shareholders",
    "plates",
    "valuation",
    "consensus",
)


class FundamentalsProvider(Protocol):
    name: str

    def fetch(self, dataset: str, ticker: str) -> dict[str, Any]: ...

    def close(self) -> None: ...


def _max_date(values: list[Any]) -> str | None:
    best: datetime | None = None
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        normalized = value.strip().replace("/", "-")[:10]
        try:
            parsed = datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            continue
        if best is None or parsed > best:
            best = parsed
    return best.strftime("%Y-%m-%d") if best else None


def _period_end_date(period: str | None) -> str | None:
    """Convert Futu's period labels into conservative report-period end dates."""
    if not isinstance(period, str):
        return None
    match = re.fullmatch(r"\s*(\d{4})\s*/\s*([A-Za-z0-9_ -]+)\s*", period)
    if not match:
        return None
    year = int(match.group(1))
    label = match.group(2).strip().upper().replace("F10TYPE_", "")
    if label in {"FY", "ANNUAL", "7"}:
        month_day = "12-31"
    elif label in {"H1", "Q2", "Q6", "5"}:
        month_day = "06-30"
    elif label in {"Q1", "1"}:
        month_day = "03-31"
    elif label in {"Q3", "Q9", "6"}:
        month_day = "09-30"
    else:
        return None
    return f"{year}-{month_day}"


def _revenue_period_kind(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    period = str(item.get("period_text") or "").strip().upper()
    suffix = period.rsplit("/", 1)[-1].replace("F10TYPE_", "")
    financial_type = str(item.get("financial_type") or "").upper().replace("F10TYPE_", "")
    if suffix in {"FY", "ANNUAL", "7"} or financial_type in {"ANNUAL", "7"}:
        return "annual"
    if suffix in {"H1", "Q6", "5"} or financial_type in {"Q6", "5"}:
        return "interim"
    return None


def _select_revenue_periods(screen_dates: list[Any]) -> list[dict[str, Any]]:
    annual: list[dict[str, Any]] = []
    interim: list[dict[str, Any]] = []
    for item in screen_dates:
        if not isinstance(item, dict):
            continue
        kind = _revenue_period_kind(item)
        if kind == "annual":
            annual.append(item)
        elif kind == "interim":
            interim.append(item)
    annual.sort(
        key=lambda item: _period_end_date(str(item.get("period_text") or "")) or "",
        reverse=True,
    )
    interim.sort(
        key=lambda item: _period_end_date(str(item.get("period_text") or "")) or "",
        reverse=True,
    )
    return [
        *annual[:REVENUE_ANNUAL_PERIOD_LIMIT],
        *interim[:REVENUE_INTERIM_PERIOD_LIMIT],
    ]


def _dataset(
    key: str,
    payload: Any,
    *,
    available_from: str | None = None,
    currency: str | None = None,
    accounting_standard: str | None = None,
    evidence_class: str | None = None,
) -> dict[str, Any]:
    return {
        "dataset": key,
        "evidence_class": evidence_class or ("reported" if available_from else "snapshot"),
        "available_from": available_from,
        "currency": currency,
        "accounting_standard": accounting_standard,
        "payload": payload,
    }


class FutuFundamentalsProvider:
    """富途只读基本面适配器；首次调用时才连接 OpenD。"""

    name = "futu"

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or os.environ.get("WONDER_FUTU_HOST", DEFAULT_FUTU_HOST)
        self.port = int(port or os.environ.get("WONDER_FUTU_PORT", DEFAULT_FUTU_PORT))
        self._context: Any = None

    def _ctx(self) -> Any:
        if self._context is None:
            try:
                from futu import OpenQuoteContext, SysConfig
            except ImportError as error:
                raise SourceError(
                    "futu_sdk_missing", "未安装 futu-api，基本面跳过", exit_code=2, blocked=True
                ) from error
            SysConfig.enable_console_log(False)
            try:
                with socket.create_connection(
                    (self.host, self.port), timeout=OPEND_PROBE_TIMEOUT_SECONDS
                ):
                    pass
            except OSError as error:
                raise SourceError(
                    "opend_unavailable",
                    f"无法连接 OpenD {self.host}:{self.port}：{error}",
                    blocked=True,
                ) from error
            try:
                self._context = OpenQuoteContext(host=self.host, port=self.port)
            except Exception as error:
                raise SourceError(
                    "opend_unavailable",
                    f"无法连接 OpenD {self.host}:{self.port}：{error}",
                    blocked=True,
                ) from error
        return self._context

    def close(self) -> None:
        if self._context is not None:
            with contextlib.suppress(Exception):
                self._context.close()
            self._context = None

    def fetch(self, dataset: str, ticker: str) -> dict[str, Any]:
        if dataset not in DATASETS:
            raise SourceError("unknown_dataset", f"不支持的数据集：{dataset}", exit_code=2)
        return getattr(self, f"_fetch_{dataset}")(ticker)

    def _ok(self, result: Any, label: str) -> Any:
        from futu import RET_OK

        ret, value = result
        if ret != RET_OK:
            raise SourceError("futu_query_failed", f"{label} 失败：{value}")
        return value

    def _fetch_profile(self, ticker: str) -> dict[str, Any]:
        frame = self._ok(self._ctx().get_company_profile(ticker), "get_company_profile")
        return _dataset("profile", _records(frame))

    def _fetch_executives(self, ticker: str) -> dict[str, Any]:
        frame = self._ok(self._ctx().get_company_executives(ticker), "get_company_executives")
        records = _records(frame)
        return _dataset("executives", records, available_from=_max_date([r.get("issue_date_str") for r in records]))

    def _fetch_operational_efficiency(self, ticker: str) -> dict[str, Any]:
        raw = self._ok(
            self._ctx().get_company_operational_efficiency(ticker, num=FINANCIAL_RECORD_LIMIT),
            "get_company_operational_efficiency",
        )
        raw = raw if isinstance(raw, dict) else {}
        items = [_json_safe(item) for item in raw.get("item_list", [])]
        return _dataset(
            "operational_efficiency",
            {"item_list": items, "currency_code": raw.get("currency_code")},
            available_from=_max_date([item.get("end_date_str") for item in items]),
            currency=raw.get("currency_code"),
        )

    def _fetch_statements(self, ticker: str) -> dict[str, Any]:
        from futu import RET_OK
        from futu.common.constant import F10Type

        statements: dict[str, Any] = {}
        dates: list[Any] = []
        currency = None
        standard = None
        for statement_type, label in ((1, "利润表"), (2, "资产负债表"), (3, "现金流量表"), (4, "关键指标")):
            ret, data = self._ctx().get_financials_statements(
                ticker,
                statement_type=statement_type,
                financial_type=F10Type.ANNUAL,
                num=FINANCIAL_RECORD_LIMIT,
            )
            if ret != RET_OK:
                raise SourceError(
                    "futu_query_failed",
                    f"get_financials_statements(statement_type={statement_type}) 失败：{data}",
                )
            raw = data if isinstance(data, dict) else {}
            reports = [_json_safe(row) for row in raw.get("report_list", [])]
            dates.extend(row.get("date_time_str") for row in reports)
            if reports and currency is None:
                currency = reports[0].get("currency_code")
                standard = reports[0].get("accounting_standards")
            statements[str(statement_type)] = {
                "label": label,
                "structure_list": [_json_safe(item) for item in raw.get("structure_list", [])],
                "report_list": reports,
            }
        return _dataset(
            "statements",
            {"statements": statements, "record_limit": FINANCIAL_RECORD_LIMIT},
            available_from=_max_date(dates),
            currency=currency,
            accounting_standard=standard,
        )

    def _fetch_revenue_breakdown(self, ticker: str) -> dict[str, Any]:
        context = self._ctx()
        raw = self._ok(
            context.get_financials_revenue_breakdown(ticker),
            "get_financials_revenue_breakdown",
        )
        raw = raw if isinstance(raw, dict) else {}
        screen_dates = raw.get("screen_date_list") or []
        selected_periods = _select_revenue_periods(screen_dates)
        historical: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        dates: list[Any] = [_period_end_date(raw.get("period"))]
        for item in selected_periods:
            period_text = str(item.get("period_text") or "")
            dates.append(_period_end_date(period_text))
            if period_text == raw.get("period"):
                period_data = raw
            else:
                try:
                    request_date = item.get("date")
                    if isinstance(request_date, str) and request_date.isdigit():
                        request_date = int(request_date)
                    if not isinstance(request_date, int):
                        failures.append(
                            {"period": period_text, "message": "Futu 历史筛选项缺少有效 date"}
                        )
                        continue
                    period_data = self._ok(
                        context.get_financials_revenue_breakdown(ticker, date=request_date),
                        f"get_financials_revenue_breakdown({period_text})",
                    )
                except SourceError as error:
                    failures.append(
                        {"period": period_text, "code": error.code, "message": str(error)}
                    )
                    continue
            period_data = period_data if isinstance(period_data, dict) else {}
            historical.append(
                {
                    "date": item.get("date"),
                    "period": period_data.get("period") or period_text,
                    "financial_type": item.get("financial_type"),
                    "currency_code": period_data.get("currency_code"),
                    "breakdown_list": [
                        _json_safe(group) for group in period_data.get("breakdown_list", [])
                    ],
                }
            )
        return _dataset(
            "revenue_breakdown",
            {
                "period": raw.get("period"),
                "currency_code": raw.get("currency_code"),
                "breakdown_list": [_json_safe(item) for item in raw.get("breakdown_list", [])],
                "available_periods": [_json_safe(item) for item in screen_dates],
                "historical_breakdowns": historical,
                "history_failures": failures,
            },
            available_from=_max_date([date for date in dates if date]),
            currency=raw.get("currency_code"),
        )

    def _fetch_dividends(self, ticker: str) -> dict[str, Any]:
        raw = self._ok(self._ctx().get_corporate_actions_dividends(ticker), "get_corporate_actions_dividends")
        raw = raw if isinstance(raw, dict) else {}
        items = [_json_safe(item) for item in raw.get("dividend_list", [])]
        return _dataset("dividends", {"dividend_list": items}, available_from=_max_date([i.get("pub_date") for i in items]))

    def _fetch_buybacks(self, ticker: str) -> dict[str, Any]:
        raw = self._ok(
            self._ctx().get_corporate_actions_buybacks(ticker, num=BUYBACK_RECORD_LIMIT),
            "get_corporate_actions_buybacks",
        )
        raw = raw if isinstance(raw, dict) else {}
        hk = raw.get("hk_buy_back_list")
        hk_records = _records(hk) if hasattr(hk, "to_dict") else hk
        return _dataset("buybacks", {"hk_buy_back_list": hk_records})

    def _fetch_shareholders(self, ticker: str) -> dict[str, Any]:
        raw = self._ok(self._ctx().get_shareholders_overview(ticker), "get_shareholders_overview")
        raw = raw if isinstance(raw, dict) else {}
        main = raw.get("main_holder")
        main_records = _records(main) if hasattr(main, "to_dict") else main
        holder_type = raw.get("holder_type")
        type_records = _records(holder_type) if hasattr(holder_type, "to_dict") else holder_type
        return _dataset(
            "shareholders",
            {"main_holder": main_records, "holder_type": type_records},
            available_from=_max_date(
                [r.get("static_date_str") for r in (main_records or []) if isinstance(r, dict)]
            ),
        )

    def _fetch_plates(self, ticker: str) -> dict[str, Any]:
        frame = self._ok(self._ctx().get_owner_plate([ticker]), "get_owner_plate")
        return _dataset("plates", _records(frame))

    def _fetch_valuation(self, ticker: str) -> dict[str, Any]:
        raw = self._ok(self._ctx().get_valuation_detail(ticker), "get_valuation_detail")
        return _dataset("valuation", _json_safe(raw))

    def _fetch_consensus(self, ticker: str) -> dict[str, Any]:
        raw = self._ok(
            self._ctx().get_research_analyst_consensus(ticker), "get_research_analyst_consensus"
        )
        return _dataset("consensus", _json_safe(raw))


def make_provider(host: str | None = None, port: int | None = None) -> FutuFundamentalsProvider:
    return FutuFundamentalsProvider(host=host, port=port)
