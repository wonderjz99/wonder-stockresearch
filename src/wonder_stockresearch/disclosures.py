"""阶段 4 公告/定期报告发现。

港交所（HKEXnews）与美国 SEC EDGAR 都走公开官方渠道；下载统一经 sources.fetch_url，
因此继承 SSRF、超时、重试与大小限制。不抓取需要登录或付费的内容。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from . import sources

HKEX_BASE = "https://www1.hkexnews.hk"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
HONG_KONG = timezone(timedelta(hours=8))
DEFAULT_LOOKBACK_YEARS = 3
MAX_ANNUAL_REPORTS = 3
MAX_CANDIDATES = 20
RELEVANT_CATEGORIES = {"annual", "interim", "quarterly", "results"}

_ANNUAL_MARKERS = ("ANNUAL REPORT", "年報", "年报")
_INTERIM_MARKERS = ("INTERIM REPORT", "中期報告", "中期报告")
_QUARTERLY_MARKERS = ("QUARTERLY REPORT", "季度報告", "季度报告")
_RESULTS_MARKERS = ("RESULTS", "業績", "业绩")


@dataclass(frozen=True)
class FilingCandidate:
    title: str
    url: str
    published_at: datetime | None
    category: str
    file_type: str
    timezone_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "category": self.category,
            "file_type": self.file_type,
            "timezone": self.timezone_name,
        }


class DisclosureProvider:
    name = "base"

    def list_filings(self, ticker: str, years: int = DEFAULT_LOOKBACK_YEARS) -> list[FilingCandidate]:
        raise NotImplementedError


def classify_title(title: str) -> str:
    upper = title.upper()
    if any(marker in upper for marker in _ANNUAL_MARKERS):
        return "annual"
    if any(marker in upper for marker in _INTERIM_MARKERS):
        return "interim"
    if any(marker in upper for marker in _QUARTERLY_MARKERS):
        return "quarterly"
    if any(marker in upper for marker in _RESULTS_MARKERS):
        return "results"
    return "other"


def select_filings(
    candidates: list[FilingCandidate], *, max_annual: int = MAX_ANNUAL_REPORTS
) -> list[dict[str, Any]]:
    """选取最近 max_annual 份年报和最新一期中期/季度报告，保留角色标签。"""

    def sort_key(candidate: FilingCandidate) -> datetime:
        return candidate.published_at or datetime.min.replace(tzinfo=UTC)

    annual = sorted(
        (c for c in candidates if c.category == "annual"), key=sort_key, reverse=True
    )[:max_annual]
    latest_period = sorted(
        (c for c in candidates if c.category in {"interim", "quarterly"}),
        key=sort_key,
        reverse=True,
    )[:1]
    selected: list[dict[str, Any]] = []
    for candidate in annual:
        selected.append({"role": "annual", **candidate.to_dict()})
    for candidate in latest_period:
        selected.append({"role": candidate.category, **candidate.to_dict()})
    return selected


def _fetch_text(url: str, *, user_agent: str | None = None) -> str:
    acquired = sources.fetch_url(url, user_agent=user_agent) if user_agent else sources.fetch_url(url)
    return acquired.data.decode("utf-8", errors="replace")


def _parse_jsonp(text: str) -> dict[str, Any]:
    start = text.find("(")
    end = text.rfind(")")
    payload = text[start + 1 : end] if start != -1 and end != -1 else text
    return json.loads(payload)


def _parse_hkex_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y %H:%M").replace(tzinfo=HONG_KONG)
    except ValueError:
        return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


class HkexDisclosureProvider(DisclosureProvider):
    name = "hkex"

    def __init__(self, base_url: str = HKEX_BASE) -> None:
        self.base_url = base_url

    def _lookup_stock_id(self, code5: str) -> int:
        query = urlencode(
            {"callback": "cb", "lang": "EN", "type": "A", "name": code5, "market": "SEHK"}
        )
        payload = _parse_jsonp(_fetch_text(f"{self.base_url}/search/prefix.do?{query}"))
        for info in payload.get("stockInfo", []):
            if str(info.get("code")) == code5:
                return int(info["stockId"])
        raise sources.SourceError("hkex_stock_not_found", f"HKEX 未找到股票代码：{code5}")

    def list_filings(self, ticker: str, years: int = DEFAULT_LOOKBACK_YEARS) -> list[FilingCandidate]:
        code5 = ticker.split(".", 1)[1]
        stock_id = self._lookup_stock_id(code5)
        today = datetime.now(HONG_KONG).date()
        query = urlencode(
            {
                "sortDir": "0",
                "sortByOptions": "DateTime",
                "category": "0",
                "market": "SEHK",
                "stockId": str(stock_id),
                "documentType": "-1",
                "fromDate": (today - timedelta(days=365 * years)).strftime("%Y%m%d"),
                "toDate": today.strftime("%Y%m%d"),
                "title": "",
                "searchType": "1",
                "t1code": "-2",
                "t2Gcode": "-2",
                "t2code": "-2",
                "rowRange": "1000",
                "lang": "EN",
            }
        )
        payload = json.loads(_fetch_text(f"{self.base_url}/search/titleSearchServlet.do?{query}"))
        rows = json.loads(payload.get("result") or "[]")
        candidates: list[FilingCandidate] = []
        for row in rows:
            link = row.get("FILE_LINK")
            if not link:
                continue
            title = (row.get("TITLE") or "").strip()
            category = classify_title(title)
            if category not in RELEVANT_CATEGORIES:
                continue
            candidates.append(
                FilingCandidate(
                    title=title,
                    url=f"{self.base_url}{link}",
                    published_at=_parse_hkex_time(row.get("DATE_TIME")),
                    category=category,
                    file_type=(row.get("FILE_TYPE") or "").lower(),
                    timezone_name="Asia/Hong_Kong",
                )
            )
        candidates.sort(
            key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC), reverse=True
        )
        return candidates[:MAX_CANDIDATES]


_SEC_CATEGORIES = {"10-K": "annual", "20-F": "annual", "10-Q": "quarterly"}


class SecDisclosureProvider(DisclosureProvider):
    name = "sec"

    def __init__(self, user_agent: str | None = None) -> None:
        self.user_agent = user_agent or os.environ.get("WONDER_SEC_USER_AGENT")
        if not self.user_agent:
            raise sources.SourceError(
                "sec_user_agent_missing",
                "SEC 要求带联系方式的 User-Agent；请设置 WONDER_SEC_USER_AGENT 后重试",
                exit_code=2,
                blocked=True,
            )
        self._cik_cache: dict[str, int] = {}

    def _cik_for(self, symbol: str) -> int:
        if symbol not in self._cik_cache:
            payload = json.loads(_fetch_text(SEC_TICKERS_URL, user_agent=self.user_agent))
            for entry in payload.values():
                if str(entry.get("ticker", "")).upper() == symbol:
                    self._cik_cache[symbol] = int(entry["cik_str"])
                    break
        if symbol not in self._cik_cache:
            raise sources.SourceError("sec_ticker_not_found", f"SEC 未找到 ticker：{symbol}")
        return self._cik_cache[symbol]

    def list_filings(self, ticker: str, years: int = DEFAULT_LOOKBACK_YEARS) -> list[FilingCandidate]:
        symbol = ticker.split(".", 1)[1].upper()
        cik = self._cik_for(symbol)
        payload = json.loads(
            _fetch_text(SEC_SUBMISSIONS_URL.format(cik=cik), user_agent=self.user_agent)
        )
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        documents = recent.get("primaryDocument", [])
        cutoff = datetime.now(UTC) - timedelta(days=365 * years)
        candidates: list[FilingCandidate] = []
        for form, filed, accession, document in zip(forms, dates, accessions, documents, strict=False):
            category = _SEC_CATEGORIES.get(str(form).upper())
            published_at = _parse_date(filed)
            if category is None or published_at is None or published_at < cutoff:
                continue
            accession_nodash = str(accession).replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{document}"
            candidates.append(
                FilingCandidate(
                    title=f"{form} · filed {filed}",
                    url=url,
                    published_at=published_at,
                    category=category,
                    file_type="html",
                    timezone_name="UTC",
                )
            )
        return candidates


def make_provider(ticker: str) -> DisclosureProvider:
    if ticker.startswith("HK."):
        return HkexDisclosureProvider()
    if ticker.startswith("US."):
        return SecDisclosureProvider()
    raise sources.SourceError("unsupported_market", f"不支持的 ticker：{ticker}", exit_code=2)
