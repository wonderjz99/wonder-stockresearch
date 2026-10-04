"""阶段 16：固定研究的证据包、可信计算和语义审查契约。"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from statistics import median, pstdev
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import research, source_policy, storage
from .models import (
    GATE_IDS,
    MOAT_IDS,
    QUESTION_IDS,
    DraftReview,
    EvidenceRef,
    ResearchDraft,
    Source,
    source_id_text,
)
from .questions import QUESTION_TEXT

QUESTION_GROUPS: dict[str, tuple[str, ...]] = {
    "Q01-Q04": ("Q01", "Q02", "Q03", "Q04"),
    "Q05-Q08": ("Q05", "Q06", "Q07", "Q08"),
    "Q09-Q12": ("Q09", "Q10", "Q11", "Q12"),
    "Q13-Q16": ("Q13", "Q14", "Q15", "Q16"),
    "Q17-Q20": ("Q17", "Q18", "Q19", "Q20"),
    "Q21-Q24": ("Q21", "Q22", "Q23", "Q24"),
}

# 逐题检索词既驱动 FTS5，也驱动完全确定性的关键词回退。它们刻意以披露常用的
# 中英文标题和财务行名为主，避免“收入/客户”这样的高频词挤掉财务附注和风险章节。
QUESTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Q01": ("business review", "consumer market", "home market", "government and enterprise", "商业模式", "政企", "家庭市场"),
    "Q02": ("industry demand", "telecommunications businesses", "mobile internet traffic", "行业需求", "电信业务", "流量"),
    "Q03": ("capital expenditure", "capital spending", "free cash flow", "资本开支", "资本支出", "自由现金流"),
    "Q04": ("5g-advanced", "6g", "artificial intelligence", "technology", "技术", "云", "算力"),
    "Q05": ("market share", "mobile subscriber", "broadband subscriber", "市场份额", "移动用户", "宽带用户"),
    "Q06": ("arpu", "tariff", "pricing", "资费", "套餐", "提价"),
    "Q07": ("depreciation", "employee expenses", "network operation", "cost", "折旧", "员工开支", "成本"),
    "Q08": ("brand", "customer satisfaction", "品牌", "客户满意度"),
    "Q09": ("return on equity", "roe", "净资产收益率", "归属母公司股东的权益"),
    "Q10": ("capital expenditure", "depreciation", "working capital", "资本开支", "折旧", "营运资金"),
    "Q11": ("free cash flow", "dividend", "share repurchase", "repayment", "自由现金流", "股息", "回购", "偿还借款"),
    "Q12": ("cash and cash equivalents", "borrowings", "liquidity risk", "现金及等价物", "借款", "流动性风险"),
    "Q13": ("interests of directors", "interests in shares", "shareholding", "changes in shareholding", "share scheme", "董事持股", "股权激励", "持股变动"),
    "Q14": ("capital allocation", "acquisition", "major investment", "dividend policy", "资本配置", "收购", "重大投资", "股利政策"),
    "Q15": ("restatement", "rectification", "mistake", "更正", "整改", "错误"),
    "Q16": ("senior management", "date of appointment", "biographical", "高级管理", "任命日期", "履历"),
    "Q17": ("service commitments", "customer retention", "switching", "服务承诺", "客户维系", "转换成本"),
    "Q18": ("sales channels", "distribution channels", "marketing", "渠道", "分销", "营销"),
    "Q19": ("customer retention", "renewal", "churn", "客户维系", "续约", "流失率"),
    "Q20": ("demand", "resilience", "recession", "需求", "韧性", "衰退"),
    "Q21": ("principal risks", "risk factors", "network and data security", "主要风险", "风险因素", "网络与数据安全"),
    "Q22": ("regulatory", "licence", "license", "tariff", "监管", "牌照", "资费"),
    "Q23": ("major customers", "geographical information", "concentration", "主要客户", "地域", "集中度"),
    "Q24": ("capital commitments", "contractual obligations", "credit facilities", "contingent liabilities", "资本承诺", "合约义务", "授信", "或有负债"),
}

MIN_CHUNKS_PER_QUESTION = 2
MAX_CHUNKS_PER_QUESTION = 4
MAX_CHUNKS_PER_PACK = 18
MAX_CHUNKS_PER_SOURCE = 8
MAX_PACK_CHARS = 32000
CHUNK_CHARS = 1600
CALCULATION_VERSION = "fixed-calculations-v3"
REVIEW_TEMPLATE_VERSION = "semantic-review-v2"


class InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceChunk(InternalModel):
    source_id: str
    locator: str
    source_kind: str
    support_eligible: bool
    source_rank: str = "official"
    extraction_method: str = "unknown"
    reconciliation_status: str = "not_applicable"
    content: str | None = None
    fields: dict[str, Any] | None = None
    period: str | None = None
    currency: str | None = None
    unit: str | None = None
    accounting_standard: str | None = None
    dataset: str | None = None
    content_hash: str
    truncated: bool = False


class EvidencePack(InternalModel):
    group_id: str
    question_ids: list[str]
    chunks: list[EvidenceChunk]
    question_chunks: dict[str, list[EvidenceChunk]] = Field(default_factory=dict)
    gaps: list[str]


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


def canonical_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def draft_hash(draft: ResearchDraft) -> str:
    return canonical_hash(draft)


def _source_text(root: Path, source: Source) -> str:
    path_value = source.text_path or source.raw_path
    path = root / path_value
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _plain_chunks(text: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    if "\f" in text:
        for page, content in enumerate(text.split("\f"), 1):
            for offset in range(0, len(content), CHUNK_CHARS):
                part = content[offset : offset + CHUNK_CHARS]
                if part.strip():
                    chunks.append((f"page {page}", part))
        return chunks
    lines = text.splitlines() or [text]
    start = 0
    while start < len(lines):
        selected: list[str] = []
        size = 0
        end = start
        while end < len(lines) and size + len(lines[end]) + 1 <= CHUNK_CHARS:
            selected.append(lines[end])
            size += len(lines[end]) + 1
            end += 1
        if end == start:
            selected = [lines[start][:CHUNK_CHARS]]
            end += 1
        content = "\n".join(selected)
        if content.strip():
            chunks.append((f"lines {start + 1}-{end}", content))
        start = end
    return chunks


def _source_page_entries(text: str) -> list[dict[str, str]]:
    """将原生 PDF 的页或纯文本的固定行段转成可索引条目。"""
    if "\f" in text:
        return [
            {
                "locator": f"page {number}",
                "content": page,
                "content_hash": canonical_hash(page),
            }
            for number, page in enumerate(text.split("\f"), 1)
            if page.strip()
        ]
    return [
        {
            "locator": locator,
            "content": content,
            "content_hash": canonical_hash(content),
        }
        for locator, content in _plain_chunks(text)
    ]


def _ensure_source_page_index(root: Path, sources: list[Source]) -> None:
    """按内容哈希更新已关联文本来源的页索引；不触网、不索引 provider JSON。"""
    storage.initialize(root)
    candidates: list[tuple[str, list[dict[str, str]]]] = []
    for source in sources:
        if source.kind == "provider" or source.metadata.get("dataset"):
            continue
        text = _source_text(root, source)
        if text.strip():
            candidates.append((source_id_text(source.id), _source_page_entries(text)))
    if not candidates:
        return
    with storage.project_lock(root), storage.open_db(root) as connection:
        for source_id, pages in candidates:
            expected = sorted(page["content_hash"] for page in pages)
            actual = sorted(storage.indexed_source_page_hashes(connection, source_id))
            if actual != expected:
                storage.replace_source_page_index(connection, source_id, pages)


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


def _provider_nodes(
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
        if any(key in value for key in ("display_name", "name", "period", "period_text", "date_time_str")):
            merged = dict(value)
            for key, item in context.items():
                merged.setdefault(key, item)
            found.append((path, merged))
        # 富途 financial statements 的 item_list 只给 field_id；将同一响应的
        # structure_list.display_name 下沉，才能让固定计算和问题检索按真实科目工作。
        field_names = {
            str(item.get("field_id")): item.get("display_name")
            for item in value.get("structure_list", [])
            if isinstance(item, dict) and item.get("field_id") is not None and item.get("display_name")
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
                found.extend(_provider_nodes(enriched, f"{path}.report_list[{index}]", context))
        for key, child in value.items():
            if key == "report_list" and field_names:
                continue
            found.extend(_provider_nodes(child, f"{path}.{key}", context))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_provider_nodes(child, f"{path}[{index}]", inherited))
    return found


def _score(text: str, keywords: tuple[str, ...]) -> int:
    lower = text.lower()
    return sum(lower.count(keyword.lower()) for keyword in keywords)


def _fts_query(keywords: tuple[str, ...]) -> str:
    """生成保守的 FTS5 OR 查询；关键词仍会经过下方逐字评分复核。"""
    terms = []
    for keyword in keywords:
        cleaned = re.sub(r'["\\*:^(){}\[\]]', " ", keyword).strip()
        if cleaned:
            terms.append(f'"{cleaned}"')
    return " OR ".join(terms)


def _window_around_keywords(content: str, keywords: tuple[str, ...]) -> tuple[str, bool]:
    if len(content) <= CHUNK_CHARS:
        return content, False
    lower = content.lower()
    positions = [lower.find(keyword.lower()) for keyword in keywords if lower.find(keyword.lower()) >= 0]
    start = max(0, min(positions) - CHUNK_CHARS // 3) if positions else 0
    end = min(len(content), start + CHUNK_CHARS)
    return content[start:end], start > 0 or end < len(content)


def _text_candidates(
    root: Path, sources: list[Source], question_id: str, as_of: Any
) -> list[tuple[int, EvidenceChunk]]:
    keywords = QUESTION_KEYWORDS[question_id]
    text_sources = [source for source in sources if source.kind != "provider" and not source.metadata.get("dataset")]
    if not text_sources:
        return []
    source_ids = [source_id_text(source.id) for source in text_sources]
    source_by_id = {source_id_text(source.id): source for source in text_sources}
    with storage.open_db(root) as connection:
        matches = storage.search_source_pages(
            connection, source_ids, _fts_query(keywords), limit=max(32, MAX_CHUNKS_PER_QUESTION * len(source_ids))
        )
        all_rows = storage.source_page_rows(connection, source_ids)
    # SQLite 的 unicode61 对 CJK 词界的处理随版本不同。先执行 FTS5（它会为典型英文
    # 年报标题缩小候选），随后并入同一来源的页以做逐字评分，保证不同 SQLite 版本和中文
    # 词界下也不会把页误判成“无证据”。
    present = {(str(row["source_id"]), str(row["locator"])) for row in matches}
    matches.extend(row for row in all_rows if (str(row["source_id"]), str(row["locator"])) not in present)
    candidates: list[tuple[int, EvidenceChunk]] = []
    for row in matches:
        source = source_by_id[str(row["source_id"])]
        score = _score(str(row["content"]), keywords)
        if not score:
            continue
        content, truncated = _window_around_keywords(str(row["content"]), keywords)
        candidates.append((score, EvidenceChunk(
            source_id=str(row["source_id"]), locator=str(row["locator"]),
            source_kind=source.kind,
            support_eligible=research._is_core_eligible(source, as_of)
            and source_policy.supports_eligible(source),
            source_rank=source_policy.rank_for(source).value,
            extraction_method=source_policy.extraction_for(source).value,
            reconciliation_status=source_policy.reconciliation_for(source).value,
            content=content, dataset=None, content_hash=str(row["content_hash"]), truncated=truncated,
        )))
    return candidates


def _provider_candidates(
    root: Path, sources: list[Source], question_id: str, as_of: Any
) -> list[tuple[int, EvidenceChunk]]:
    keywords = QUESTION_KEYWORDS[question_id]
    candidates: list[tuple[int, EvidenceChunk]] = []
    for source in sources:
        if source.kind != "provider" and not source.metadata.get("dataset"):
            continue
        try:
            payload = json.loads(_source_text(root, source))
        except json.JSONDecodeError:
            continue
        dataset = str(source.metadata.get("dataset") or payload.get("dataset") or "") or None
        for locator, node in _provider_nodes(payload):
            rendered = json.dumps(node, ensure_ascii=False, default=str)
            score = _score(rendered, keywords)
            if not score:
                continue
            stored_fields = node if len(rendered) <= CHUNK_CHARS else {"excerpt": rendered[:CHUNK_CHARS]}
            candidates.append((score, EvidenceChunk(
                source_id=source_id_text(source.id), locator=locator, source_kind=source.kind,
                support_eligible=research._is_core_eligible(source, as_of)
                and source_policy.supports_eligible(source),
                source_rank=source_policy.rank_for(source).value,
                extraction_method=source_policy.extraction_for(source).value,
                reconciliation_status=source_policy.reconciliation_for(source).value,
                fields=stored_fields,
                period=str(node.get("period_text") or node.get("period") or node.get("date_time_str"))
                if (node.get("period_text") or node.get("period") or node.get("date_time_str")) is not None else None,
                currency=str(node.get("currency") or node.get("currency_code"))
                if (node.get("currency") or node.get("currency_code")) else None,
                unit=str(node.get("unit")) if node.get("unit") else None,
                accounting_standard=str(node.get("accounting_standard") or node.get("accounting_standards"))
                if (node.get("accounting_standard") or node.get("accounting_standards")) else None,
                dataset=dataset, content_hash=canonical_hash(node), truncated=len(rendered) > CHUNK_CHARS,
            )))
    return candidates


def _period_match(chunk: EvidenceChunk, as_of: Any) -> int:
    """期间完整且不晚于 as_of 的候选优先；解析不了的资料保留但排序靠后。"""
    if not chunk.period:
        return 0
    text = chunk.period.replace("/FY", "-12-31").replace("/H1", "-06-30")
    try:
        period = storage.parse_iso(text if "T" in text else f"{text}T00:00:00+00:00")
        boundary = as_of if getattr(as_of, "tzinfo", None) else storage.parse_iso(str(as_of))
    except (TypeError, ValueError):
        return 0
    return 1 if period <= boundary else -1


def _select_question_chunks(
    candidates: list[tuple[int, EvidenceChunk, Source]], *, remaining_chars: int,
    used_source_chunks: dict[str, int], max_chunks: int, as_of: Any,
) -> list[EvidenceChunk]:
    chosen: list[EvidenceChunk] = []
    seen: set[tuple[str, str]] = set()
    for score, chunk, source in sorted(
        candidates,
        key=lambda item: source_policy.ranking_key(
            usable=item[1].support_eligible,
            question_match=item[0],
            source=item[2],
            period_match=_period_match(item[1], as_of),
            locator=item[1].locator,
        ),
    ):
        key = (chunk.source_id, chunk.locator)
        if key in seen or used_source_chunks.get(chunk.source_id, 0) >= MAX_CHUNKS_PER_SOURCE:
            continue
        size = len(json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False, default=str))
        if size > remaining_chars:
            continue
        chosen.append(chunk)
        seen.add(key)
        used_source_chunks[chunk.source_id] = used_source_chunks.get(chunk.source_id, 0) + 1
        remaining_chars -= size
        if len(chosen) >= max_chunks:
            break
    return chosen


def build_evidence_pack(
    root: Path, group_id: str, sources: list[Source], as_of: Any, *, indexed: bool = False,
) -> EvidencePack:
    if not indexed:
        _ensure_source_page_index(root, sources)
    question_ids = QUESTION_GROUPS[group_id]
    source_by_id = {source_id_text(source.id): source for source in sources}
    candidates = {
        question_id: [
            *[
                (score, chunk, source_by_id[chunk.source_id])
                for score, chunk in _text_candidates(root, sources, question_id, as_of)
            ],
            *[
                (score, chunk, source_by_id[chunk.source_id])
                for score, chunk in _provider_candidates(root, sources, question_id, as_of)
            ],
        ]
        for question_id in question_ids
    }
    # 第一轮为每题保留最多两个片段，不能被同组其他高频问题挤掉。
    question_chunks: dict[str, list[EvidenceChunk]] = {question_id: [] for question_id in question_ids}
    used_source_chunks: dict[str, int] = {}
    used_chars = 0
    for question_id in question_ids:
        selected = _select_question_chunks(
            candidates[question_id], remaining_chars=MAX_PACK_CHARS - used_chars,
            used_source_chunks=used_source_chunks, max_chunks=MIN_CHUNKS_PER_QUESTION, as_of=as_of,
        )
        question_chunks[question_id].extend(selected)
        used_chars += sum(len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False, default=str)) for item in selected)
    # 第二轮在仍有预算时扩充到最多四段，保持题目顺序和候选排序稳定。
    for question_id in question_ids:
        if used_chars >= MAX_PACK_CHARS:
            break
        existing = {(item.source_id, item.locator) for item in question_chunks[question_id]}
        selected = _select_question_chunks(
            [item for item in candidates[question_id] if (item[1].source_id, item[1].locator) not in existing],
            remaining_chars=MAX_PACK_CHARS - used_chars, used_source_chunks=used_source_chunks,
            max_chunks=MAX_CHUNKS_PER_QUESTION - len(question_chunks[question_id]), as_of=as_of,
        )
        question_chunks[question_id].extend(selected)
        used_chars += sum(len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False, default=str)) for item in selected)
    chosen: list[EvidenceChunk] = []
    seen: set[tuple[str, str]] = set()
    for question_id in question_ids:
        for chunk in question_chunks[question_id]:
            if (chunk.source_id, chunk.locator) not in seen and len(chosen) < MAX_CHUNKS_PER_PACK:
                chosen.append(chunk)
                seen.add((chunk.source_id, chunk.locator))
    gaps = [f"{question_id} 没有命中归档证据片段" for question_id, items in question_chunks.items() if not items]
    return EvidencePack(
        group_id=group_id, question_ids=list(question_ids), chunks=chosen,
        question_chunks=question_chunks, gaps=gaps,
    )


def build_evidence_packs(root: Path, sources: list[Source], as_of: Any) -> dict[str, EvidencePack]:
    _ensure_source_page_index(root, sources)
    return {key: build_evidence_pack(root, key, sources, as_of, indexed=True) for key in QUESTION_GROUPS}


def _numeric_inputs(sources: list[Source], root: Path) -> list[CalculationInput]:
    inputs: list[CalculationInput] = []
    for source in sources:
        if source.kind != "provider" and not source.metadata.get("dataset"):
            continue
        try:
            payload = json.loads(_source_text(root, source))
        except (json.JSONDecodeError, TypeError):
            continue
        for locator, node in _provider_nodes(payload):
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
                "roe" in normalized_label or "净资产收益率" in label_text
                or "ratio" in normalized_label or "比率" in label_text
            )
            inputs.append(CalculationInput(
                label=label_text, value=value, period=str(period),
                currency=str(node.get("currency") or node.get("currency_code"))
                if not is_rate_or_ratio and (node.get("currency") or node.get("currency_code")) else None,
                unit=("%" if "roe" in normalized_label or "净资产收益率" in label_text else str(node.get("unit")))
                if ("roe" in normalized_label or "净资产收益率" in label_text or node.get("unit")) else None,
                source_id=source_id_text(source.id), locator=locator,
            ))
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


def _annual_capex_inputs(root: Path, sources: list[Source]) -> list[CalculationInput]:
    """抽取年报中明确披露的年度 Capex；不接受计划、指引或无币种的模糊句子。"""
    values: list[CalculationInput] = []
    currency_map = {"RMB": "CNY", "HK$": "HKD", "US$": "USD"}
    scale_map = {"billion": Decimal(1_000_000_000), "million": Decimal(1_000_000), "mil": Decimal(1_000_000)}
    for source in sources:
        if source.kind != "primary" or source.metadata.get("role") != "annual":
            continue
        years = _ANNUAL_REPORT_YEAR.findall(source.title)
        text = _source_text(root, source)
        if len(years) != 1 or not text:
            continue
        period = f"{years[0]}/FY"
        # 年报摘要中的明确叙述优先于版面图表。两者通常重复同一数值；若同一优先级
        # 出现不同数值，便保守地不生成计算输入。
        matches: list[tuple[int, int, Decimal, str, str]] = []
        for page_number, page in enumerate(text.split("\f"), 1):
            for pattern in (_CAPEX_DISCLOSURE, _CAPEX_TABLE_DISCLOSURE):
                for match in pattern.finditer(page):
                    before = page[max(0, match.start() - 100):match.start()].lower()
                    if any(token in before for token in ("expect", "guidance", "预计", "预期")):
                        continue
                    raw_amount = Decimal(match.group("amount").replace(",", ""))
                    amount = raw_amount * scale_map[match.group("scale").lower()]
                    currency = currency_map[match.group("currency").upper()]
                    normalized_label = " ".join(match.group(0).lower().split())
                    # “for the year” 是管理层对当年 Capex 的完整披露，通常给出较精确的
                    # 百万元金额；摘要中的四舍五入十亿元数字次之，图表最后。
                    priority = (
                        0 if "for the year" in normalized_label
                        else 1 if pattern is _CAPEX_DISCLOSURE
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
            if len(currencies) != 1 or not min(amounts) or max(amounts) / min(amounts) - 1 > Decimal("0.005"):
                continue
            max_precision = max(item[1] for item in preferred)
            preferred = [item for item in preferred if item[1] == max_precision]
            unique_values = {(amount, currency) for _, _, amount, currency, _ in preferred}
        if len(unique_values) != 1:
            continue
        amount, currency = next(iter(unique_values))
        locator = next(locator for _, _, candidate_amount, candidate_currency, locator in preferred
                       if (candidate_amount, candidate_currency) == (amount, currency))
        values.append(CalculationInput(
            label="reported capital expenditure", value=amount, period=period, currency=currency,
            unit="currency", source_id=source_id_text(source.id), locator=locator,
        ))
    return values


def _summary(values: list[Decimal]) -> dict[str, Any]:
    if not values:
        return {}
    return {
        "min": min(values), "max": max(values), "median": Decimal(str(median(values))),
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
    labels = ("购买固定资产", "购买物业、厂房及设备", "购买无形资产", "purchase fixed asset", "purchase intangible")
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
        values.append({
            "period": period,
            "value": sum((abs(item.value) for item in items), Decimal(0)),
            "unit": "reported currency",
            "currency": next(iter(currencies)),
            "basis": "cash-flow proxy: purchases of fixed assets and intangible assets",
        })
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


def calculate_trusted_metrics(root: Path, sources: list[Source]) -> list[CalculationResult]:
    all_inputs = [*_numeric_inputs(sources, root), *_annual_capex_inputs(root, sources)]
    results: list[CalculationResult] = []
    profits = _preferred_series(all_inputs, ("归属母公司净利润", "净利润", "profit attributable", "net profit"))
    growth_values: list[dict[str, Any]] = []
    for previous, current in pairwise(profits):
        if previous.value != 0 and previous.currency == current.currency:
            growth_values.append({"period": current.period, "value": (current.value / previous.value - 1) * 100, "unit": "%"})
    if profits:
        vals = [Decimal(str(item["value"])) for item in growth_values]
        results.append(CalculationResult(metric="net_profit_yoy_growth", formula="(current/prior-1)*100", values=growth_values, inputs=profits, source_refs=_refs(profits), missing_periods=[] if growth_values else ["需要至少两个可比期间"], summary=_summary(vals)))
    roes = _preferred_series(all_inputs, ("净资产收益率(roe)", "roe", "净资产收益率"))
    if roes:
        values = [{"period": item.period, "value": item.value, "unit": item.unit or "%"} for item in roes]
        results.append(CalculationResult(metric="roe_series", formula="reported ROE series", values=values, inputs=roes, source_refs=_refs(roes), summary={**_summary([i.value for i in roes]), "covered_years": len(roes), "years_above_15_percent": sum(i.value > 15 for i in roes)}))
    revenues = {item.period: item for item in _preferred_series(all_inputs, ("营业总收入", "营业收入", "revenue"))}
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
        results.append(CalculationResult(metric="capex_to_revenue", formula="capex/revenue*100", values=ratios, inputs=ratio_inputs, source_refs=_refs(ratio_inputs), missing_periods=missing, incomparable_reasons=incomparable, summary=_summary([Decimal(str(i["value"])) for i in ratios])))
    cash_capex = _cash_capex_proxy(all_inputs)
    if cash_capex is not None:
        results.append(cash_capex)
    return results


def validate_review(review: DraftReview, draft: ResearchDraft, source_ids: set[str]) -> list[research.ValidationIssue]:
    issues: list[research.ValidationIssue] = []
    if review.draft_hash != draft_hash(draft):
        issues.append(research.ValidationIssue("semantic_review_stale", "review.draft_hash", "审查对应的草稿哈希与当前草稿不一致"))
    for actual, expected, location in (
        (review.checked_questions, QUESTION_IDS, "review.checked_questions"),
        (review.checked_gates, GATE_IDS, "review.checked_gates"),
        (review.checked_moats, MOAT_IDS, "review.checked_moats"),
    ):
        if set(actual) != set(expected) or len(actual) != len(expected):
            issues.append(research.ValidationIssue("semantic_review_incomplete", location, "语义审查覆盖清单不完整或有重复"))
    for index, item in enumerate(review.issues):
        for ref in item.evidence:
            if source_id_text(ref.source_id) not in source_ids:
                issues.append(research.ValidationIssue("semantic_review_illegal_reference", f"review.issues[{index}].evidence", "审查引用不属于当前 run"))
    if review.status == "failed" or any(item.severity == "error" for item in review.issues):
        issues.append(research.ValidationIssue("semantic_review_failed", "review", "当前草稿仍有语义审查 error"))
    return issues


def review_prompt_payload(draft: ResearchDraft, packs: dict[str, EvidencePack], calculations: list[CalculationResult]) -> dict[str, Any]:
    statements = [statement for answer in draft.answers for statement in answer.statements]
    statements.extend(gate.rationale for gate in draft.gates)
    statements.extend(moat.rationale for moat in draft.moats)
    statements.extend((draft.challenge.counterargument, draft.conclusion))
    statements.extend(item.statement for item in draft.review_explanations)
    cited = {
        (source_id_text(ref.source_id), ref.locator)
        for statement in statements
        for ref in [*statement.evidence, *(evidence for number in statement.numbers for evidence in number.evidence)]
    }
    compact_packs = {
        key: [
            chunk.model_dump(mode="json")
            for chunk in pack.chunks
            if (chunk.source_id, chunk.locator) in cited
        ]
        for key, pack in packs.items()
    }
    return {
        "draft_hash": draft_hash(draft),
        "questions": QUESTION_TEXT,
        "draft": draft.model_dump(mode="json"),
        "cited_evidence": compact_packs,
        "trusted_calculations": [item.model_dump(mode="json") for item in calculations],
    }
