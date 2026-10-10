"""自然语言输入的上市公司标的解析。

候选搜索只负责发现和消歧，不归档为研究来源，也不代表用户已经选择标的。
真实标的优先来自项目本地清单或富途只读搜索；模型候选始终标记为未验证。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import envfile, market, model_request, storage
from .models import SymbolCandidate, validate_ticker
from .sources import SourceError

MAX_CANDIDATES = 10
_SPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[\s\-_/··,，。.!！?？()（）]")


@dataclass(frozen=True)
class ResolutionResult:
    candidates: list[SymbolCandidate]
    provider_error: str | None = None
    model_error: str | None = None


def normalize_query(value: str) -> str:
    return _PUNCTUATION_RE.sub("", _SPACE_RE.sub("", value.casefold())).strip()


def _market_name(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    if normalized in {"HK", "HKEX", "SEHK"} or "HK" in normalized:
        return "HK"
    if normalized in {"US", "NASDAQ", "NYSE"} or normalized.startswith("US"):
        return "US"
    return None


def _ticker_for_record(market_name: str, value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw:
        return None
    if raw.startswith(("HK.", "US.")):
        ticker = raw
    elif market_name == "HK" and raw.isdigit():
        ticker = f"HK.{raw.zfill(5)}"
    elif market_name == "US":
        ticker = f"US.{raw.replace('US.', '')}"
    else:
        return None
    try:
        return validate_ticker(ticker)
    except ValueError:
        return None


def _is_common_stock(value: Any) -> bool:
    normalized = str(value or "").strip().casefold()
    return normalized in {"stock", "equity", "common stock", "普通股", "股票"}


def _candidate(
    *,
    candidate_id: str,
    ticker: str,
    name: str,
    market_name: str,
    security_type: str,
    source: str,
    verified: bool,
) -> SymbolCandidate:
    return SymbolCandidate(
        candidate_id=candidate_id,
        ticker=ticker,
        name=name,
        market=market_name,
        security_type=security_type or "stock",
        source=source,
        verified=verified,
    )


def _sort_candidates(query: str, candidates: list[SymbolCandidate]) -> list[SymbolCandidate]:
    normalized = normalize_query(query)

    def key(item: SymbolCandidate) -> tuple[int, int, str, str]:
        name = normalize_query(item.name)
        exact = 0 if name == normalized else 1 if normalized and normalized in name else 2
        market = 0 if item.market == "HK" else 1
        return exact, market, name, item.ticker

    deduplicated: dict[str, SymbolCandidate] = {}
    for item in sorted(candidates, key=key):
        deduplicated.setdefault(item.ticker, item)
    sorted_items = sorted(deduplicated.values(), key=key)[:MAX_CANDIDATES]
    return [item.model_copy(update={"candidate_id": str(index)}) for index, item in enumerate(sorted_items, 1)]


def _local_candidates(root: Path, query: str) -> list[SymbolCandidate]:
    normalized = normalize_query(query)
    if not normalized:
        return []
    storage.initialize(root)
    result: list[SymbolCandidate] = []
    with storage.open_db(root) as connection:
        rows = storage.list_stocks(connection, watched_only=False)
    for row in rows:
        name = str(row.get("name") or "").strip()
        ticker = str(row.get("ticker") or "").strip().upper()
        market_name = _market_name(ticker)
        if not name or market_name is None:
            continue
        if normalized not in normalize_query(name) and normalized not in normalize_query(ticker):
            continue
        try:
            validate_ticker(ticker)
        except ValueError:
            continue
        result.append(
            _candidate(
                candidate_id="0",
                ticker=ticker,
                name=name,
                market_name=market_name,
                security_type="stock",
                source="local",
                verified=True,
            )
        )
    return result


def _futu_candidates(query: str, provider: Any) -> list[SymbolCandidate]:
    result: list[SymbolCandidate] = []
    normalized_query = normalize_query(query)
    for row in provider.search_quotes(query, max_count=MAX_CANDIDATES):
        market_name = _market_name(row.get("market"))
        name = str(row.get("name") or "").strip()
        security_type = str(row.get("sec_type") or "").strip()
        normalized_name = normalize_query(name)
        normalized_code = normalize_query(str(row.get("code") or ""))
        ticker = _ticker_for_record(market_name or "", row.get("code")) if market_name else None
        if not market_name or not ticker or not name or not _is_common_stock(security_type):
            continue
        if normalized_query and normalized_query not in normalized_name and normalized_query not in normalized_code:
            continue
        result.append(
            _candidate(
                candidate_id="0",
                ticker=ticker,
                name=name,
                market_name=market_name,
                security_type=security_type,
                source="futu",
                verified=True,
            )
        )
    return result


def _model_candidates(root: Path, query: str, model: str | None) -> list[SymbolCandidate]:
    """请求未验证的候选；模型不能成为已验证证券目录。"""
    envfile.load_env_file(root / ".env")
    prompt = (
        "你是上市公司名称消歧助手。只返回一个 JSON 对象："
        '{"candidates":[{"name":"公司全称","ticker":"HK.00000",'
        '"market":"HK","security_type":"stock"}]}。'
        "用户搜索词可能是简称，最多给出 5 个港股或美股普通股候选；"
        "不确定的候选不要写；不能确认 ticker 时不要编造。"
        "这些候选只供用户选择，不能表示已由行情数据源验证。"
    )
    response = model_request.request_deepseek(
        api_key=model_request.api_key(),
        model=model_request.resolved_model(model),
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": query.strip()},
        ],
        temperature=0.0,
        thinking="disabled",
    )
    content = model_request.extract_content(response)
    payload = json.loads(content)
    raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(raw_candidates, list):
        return []
    result: list[SymbolCandidate] = []
    for item in raw_candidates[:MAX_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        candidate_id = "0"
        name = str(item.get("name") or "").strip()
        raw_ticker = str(item.get("ticker") or "").strip().upper()
        market_name = _market_name(item.get("market"))
        security_type = str(item.get("security_type") or "stock").strip()
        ticker = _ticker_for_record(market_name or "", raw_ticker) if market_name else None
        if not name or not market_name or not ticker:
            continue
        if not _is_common_stock(security_type):
            continue
        result.append(
            _candidate(
                candidate_id=candidate_id,
                ticker=ticker,
                name=name,
                market_name=market_name,
                security_type=security_type,
                source="model_suggestion",
                verified=False,
            )
        )
    return result


def resolve(
    root: Path,
    query: str,
    *,
    model: str | None = None,
    provider_factory: Callable[[], Any] | None = None,
) -> ResolutionResult:
    """按本地清单→富途→模型的顺序解析公司简称。"""
    local = _sort_candidates(query, _local_candidates(root, query))
    if local:
        return ResolutionResult(local)

    provider_error: str | None = None
    provider = None
    try:
        factory = provider_factory or market.make_provider
        provider = factory()
        futu = _sort_candidates(query, _futu_candidates(query, provider))
        if futu:
            return ResolutionResult(futu)
    except (SourceError, OSError, ValueError) as error:
        provider_error = str(error)
    except (RuntimeError, TypeError) as error:  # 第三方 SDK 可能抛出未包装异常；继续模型兜底。
        provider_error = f"{type(error).__name__}: {error}"
    finally:
        if provider is not None:
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    try:
        model_items = _sort_candidates(query, _model_candidates(root, query, model))
    except (model_request.ModelRequestError, json.JSONDecodeError, TypeError, ValueError) as error:
        return ResolutionResult([], provider_error=provider_error, model_error=str(error))
    return ResolutionResult(model_items, provider_error=provider_error)
