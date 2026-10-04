"""阶段 4 行情/财务只读适配器。

只使用 OpenD 的行情上下文（OpenQuoteContext），不导入、不调用任何账户、
持仓、资金或交易接口。futu-api 采用惰性 import，离线导入本模块不连接服务。
"""

from __future__ import annotations

import contextlib
import math
import os
import socket
from datetime import date, datetime
from typing import Any, Protocol

from .sources import SourceError

DEFAULT_FUTU_HOST = "127.0.0.1"
DEFAULT_FUTU_PORT = 11111
FINANCIAL_RECORD_LIMIT = 10
OPEND_PROBE_TIMEOUT_SECONDS = 2.0
# get_financials_statements 的 statement_type：1 利润表 2 资产负债表 3 现金流量表 4 关键指标。
FINANCIAL_STATEMENT_TYPES = {1: "利润表", 2: "资产负债表", 3: "现金流量表", 4: "关键指标"}


class QuoteProvider(Protocol):
    name: str

    def search_quotes(self, keyword: str, max_count: int = 10) -> list[dict[str, Any]]: ...

    def market_snapshot(self, ticker: str) -> dict[str, Any]: ...

    def financial_statements(self, ticker: str) -> dict[str, Any]: ...

    def close(self) -> None: ...


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(entry) for key, entry in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(entry) for entry in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except (ValueError, TypeError):
            return str(value)
    return str(value)


def _records(frame: Any) -> list[dict[str, Any]]:
    """把 pandas DataFrame 或字典列表转成 JSON 安全的原始记录，字段名原样保留。"""
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        raw = frame.to_dict(orient="records")
    elif isinstance(frame, list):
        raw = frame
    else:
        raw = []
    return [{str(key): _json_safe(value) for key, value in dict(record).items()} for record in raw]


class FutuQuoteProvider:
    """富途 OpenD 只读适配器；首次调用时才连接。"""

    name = "futu"

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or os.environ.get("WONDER_FUTU_HOST", DEFAULT_FUTU_HOST)
        self.port = int(port or os.environ.get("WONDER_FUTU_PORT", DEFAULT_FUTU_PORT))
        self._context: Any = None

    def _probe_opend(self) -> None:
        """先探测 TCP 端口，避免第三方 SDK 在没有 OpenD 时长时间阻塞。"""
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=OPEND_PROBE_TIMEOUT_SECONDS
            ):
                return
        except OSError as error:
            raise SourceError(
                "opend_unavailable",
                f"无法连接 OpenD {self.host}:{self.port}：{error}",
                blocked=True,
            ) from error

    def _ctx(self) -> Any:
        if self._context is None:
            try:
                from futu import OpenQuoteContext, SysConfig
            except ImportError as error:
                raise SourceError(
                    "futu_sdk_missing", "未安装 futu-api，行情/财务跳过", exit_code=2, blocked=True
                ) from error
            # futu SDK 默认把日志写到 stdout，会污染 CLI 的 JSON 契约，这里关闭控制台日志。
            SysConfig.enable_console_log(False)
            self._probe_opend()
            try:
                self._context = OpenQuoteContext(host=self.host, port=self.port)
            except Exception as error:
                raise SourceError(
                    "opend_unavailable",
                    f"无法连接 OpenD {self.host}:{self.port}：{error}",
                    blocked=True,
                ) from error
        return self._context

    def market_snapshot(self, ticker: str) -> dict[str, Any]:
        from futu import RET_OK

        ret, data = self._ctx().get_market_snapshot([ticker])
        if ret != RET_OK:
            raise SourceError("market_snapshot_failed", f"get_market_snapshot 失败：{data}")
        return {
            "code": ticker,
            "endpoint": "OpenQuoteContext.get_market_snapshot",
            "records": _records(data),
        }

    def search_quotes(self, keyword: str, max_count: int = 10) -> list[dict[str, Any]]:
        """按名称/代码搜索标的；只保留搜索结果，不读取用户自选状态。"""
        from futu import RET_OK

        if not keyword.strip():
            raise SourceError("empty_symbol_query", "标的搜索词不能为空", exit_code=2)
        if max_count < 1:
            raise SourceError("invalid_symbol_limit", "标的搜索数量必须大于 0", exit_code=2)
        ret, data = self._ctx().get_search_quote(keyword.strip(), max_count=max_count)
        if ret != RET_OK:
            raise SourceError("search_quote_failed", f"get_search_quote 失败：{data}")
        records = _records(data)
        # Futu 返回的 is_watched 是用户侧状态；候选解析明确不读取或保存它。
        return [
            {
                "market": item.get("market"),
                "code": item.get("code"),
                "name": item.get("name"),
                "sec_type": item.get("sec_type"),
            }
            for item in records
        ]

    def financial_statements(self, ticker: str) -> dict[str, Any]:
        from futu import RET_OK
        from futu.common.constant import F10Type

        context = self._ctx()
        statements: dict[str, Any] = {}
        for statement_type, label in FINANCIAL_STATEMENT_TYPES.items():
            ret, data = context.get_financials_statements(
                ticker,
                statement_type=statement_type,
                financial_type=F10Type.ANNUAL,
                num=FINANCIAL_RECORD_LIMIT,
            )
            if ret != RET_OK:
                raise SourceError(
                    "financial_statements_failed",
                    f"get_financials_statements(statement_type={statement_type}) 失败：{data}",
                )
            raw = data if isinstance(data, dict) else {}
            statements[str(statement_type)] = {
                "label": label,
                "next_key": raw.get("next_key"),
                "structure_list": [_json_safe(item) for item in raw.get("structure_list", [])],
                "report_list": [_json_safe(item) for item in raw.get("report_list", [])],
            }
        return {
            "code": ticker,
            "endpoint": "OpenQuoteContext.get_financials_statements",
            "financial_type": "ANNUAL",
            "record_limit": FINANCIAL_RECORD_LIMIT,
            "unit_note": "futu 未提供 item 级单位，保留原始 data 值，不推断单位",
            "statements": statements,
        }

    def close(self) -> None:
        if self._context is not None:
            with contextlib.suppress(Exception):
                self._context.close()
            self._context = None


def make_provider(host: str | None = None, port: int | None = None) -> FutuQuoteProvider:
    return FutuQuoteProvider(host=host, port=port)
