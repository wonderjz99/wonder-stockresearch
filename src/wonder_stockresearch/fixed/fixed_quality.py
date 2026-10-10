"""阶段 16：固定研究的证据包、可信计算和语义审查契约。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from wonder_stockresearch import financial_calculations, source_policy, storage
from wonder_stockresearch.fixed import research
from wonder_stockresearch.fixed.questions import MOAT_DEFINITIONS, MOAT_SCORE_ANCHORS, QUESTION_TEXT
from wonder_stockresearch.models import (
    GATE_IDS,
    MOAT_IDS,
    QUESTION_IDS,
    DraftReview,
    ResearchDraft,
    Source,
    source_id_text,
)

# 固定研究保留原计算 API；正式报告与交互工作区共用一份实现和模型。
CALCULATION_VERSION = financial_calculations.CALCULATION_VERSION
CalculationInput = financial_calculations.CalculationInput
CalculationResult = financial_calculations.CalculationResult
InternalModel = financial_calculations.InternalModel
calculate_trusted_metrics = financial_calculations.calculate_trusted_metrics
_numeric_inputs = financial_calculations.numeric_inputs
_annual_capex_inputs = financial_calculations.annual_capex_inputs
_provider_nodes = financial_calculations.provider_nodes
_source_text = financial_calculations.source_text

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
    "Q01": (
        "business review",
        "segment revenue",
        "revenue breakdown",
        "main_oper_income",
        "consumer market",
        "home market",
        "government and enterprise",
        "商业模式",
        "主营构成",
        "政企",
        "家庭市场",
    ),
    "Q02": (
        "industry demand",
        "telecommunications businesses",
        "mobile internet traffic",
        "营业总收入",
        "行业需求",
        "电信业务",
        "流量",
    ),
    "Q03": (
        "capital expenditure",
        "capital spending",
        "free cash flow",
        "资本开支",
        "资本支出",
        "自由现金流",
    ),
    "Q04": (
        "5g-advanced",
        "6g",
        "artificial intelligence",
        "product life cycle",
        "technological iteration",
        "research and development",
        "产品生命周期",
        "技术迭代",
        "研发",
        "算力",
    ),
    "Q05": (
        "market share",
        "mobile subscriber",
        "broadband subscriber",
        "市场份额",
        "移动用户",
        "宽带用户",
    ),
    "Q06": ("arpu", "tariff", "pricing", "资费", "套餐", "提价"),
    "Q07": (
        "depreciation",
        "employee expenses",
        "network operation",
        "cost",
        "折旧",
        "员工开支",
        "成本",
    ),
    "Q08": ("brand", "customer satisfaction", "品牌", "客户满意度"),
    "Q09": ("return on equity", "roe", "净资产收益率", "归属母公司股东的权益"),
    "Q10": (
        "capital expenditure",
        "depreciation",
        "working capital",
        "资本开支",
        "折旧",
        "营运资金",
    ),
    "Q11": (
        "free cash flow",
        "dividend",
        "share repurchase",
        "repayment",
        "自由现金流",
        "股息",
        "回购",
        "偿还借款",
    ),
    "Q12": (
        "cash and cash equivalents",
        "borrowings",
        "liquidity risk",
        "现金及等价物",
        "借款",
        "流动性风险",
    ),
    "Q13": (
        "interests of directors",
        "interests in shares",
        "shareholding",
        "holder_pct",
        "main_holder",
        "changes in shareholding",
        "share scheme",
        "董事持股",
        "股权激励",
        "持股变动",
    ),
    "Q14": (
        "capital allocation",
        "acquisition",
        "major investment",
        "dividend policy",
        "buy_back_money",
        "dividend_list",
        "资本配置",
        "收购",
        "重大投资",
        "股利政策",
    ),
    "Q15": ("restatement", "rectification", "mistake", "更正", "整改", "错误"),
    "Q16": (
        "senior management",
        "date of appointment",
        "biographical",
        "begin_date",
        "position_name",
        "高级管理",
        "任命日期",
        "履历",
    ),
    "Q17": (
        "service commitments",
        "customer retention",
        "switching",
        "ecosystem",
        "integrated services",
        "bundle",
        "服务承诺",
        "客户维系",
        "转换成本",
        "融合率",
        "生态",
    ),
    "Q18": ("sales channels", "distribution channels", "marketing", "渠道", "分销", "营销"),
    "Q19": (
        "customer retention",
        "renewal",
        "churn",
        "monthly active",
        "fee-based subscriptions",
        "arpu",
        "客户维系",
        "续约",
        "流失率",
        "月活",
        "付费会员",
    ),
    "Q20": ("demand", "resilience", "recession", "需求", "韧性", "衰退"),
    "Q21": (
        "principal risks",
        "risk factors",
        "network and data security",
        "主要风险",
        "风险因素",
        "网络与数据安全",
    ),
    "Q22": (
        "regulatory environment",
        "government regulation",
        "operating licence",
        "business licence",
        "game approval",
        "spectrum",
        "tariff regulation",
        "监管环境",
        "经营牌照",
        "版号",
        "频谱",
        "反垄断",
        "资费监管",
    ),
    "Q23": (
        "major customers",
        "geographical information",
        "revenue breakdown",
        "main_oper_income",
        "concentration",
        "主要客户",
        "主营构成",
        "地域",
        "集中度",
    ),
    "Q24": (
        "capital commitments",
        "contractual obligations",
        "credit facilities",
        "contingent liabilities",
        "资本承诺",
        "合约义务",
        "授信",
        "或有负债",
    ),
}

# Futu 的 dataset 名本身就是稳定的结构语义。给匹配数据集一个有限加分，避免年报里
# 偶然重复的通用词把股东、管理层、主营构成等结构化记录全部挤出证据包。
QUESTION_DATASET_BONUS: dict[str, dict[str, int]] = {
    "Q01": {"revenue_breakdown": 6},
    "Q02": {"statements": 3},
    "Q03": {"statements": 3},
    "Q07": {"statements": 3, "operational_efficiency": 4},
    "Q09": {"statements": 8},
    "Q10": {"statements": 5},
    "Q11": {"statements": 4, "dividends": 7, "buybacks": 7},
    "Q12": {"statements": 6},
    "Q13": {"shareholders": 10, "executives": 5},
    "Q14": {"buybacks": 8, "dividends": 8},
    "Q16": {"executives": 10},
    "Q23": {"revenue_breakdown": 10},
    "Q24": {"statements": 6},
}

MIN_CHUNKS_PER_QUESTION = 2
MAX_CHUNKS_PER_QUESTION = 4
MAX_CHUNKS_PER_QUESTION_SOURCE = 2
MAX_CHUNKS_PER_PACK = 18
MAX_CHUNKS_PER_SOURCE = 8
MAX_PACK_CHARS = 32000
CHUNK_CHARS = 1600
REVIEW_TEMPLATE_VERSION = "semantic-review-v2"


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


def canonical_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def draft_hash(draft: ResearchDraft) -> str:
    return canonical_hash(draft)


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
    positions = [
        lower.find(keyword.lower()) for keyword in keywords if lower.find(keyword.lower()) >= 0
    ]
    start = max(0, min(positions) - CHUNK_CHARS // 3) if positions else 0
    end = min(len(content), start + CHUNK_CHARS)
    return content[start:end], start > 0 or end < len(content)


def _text_candidates(
    root: Path, sources: list[Source], question_id: str, as_of: Any
) -> list[tuple[int, EvidenceChunk]]:
    keywords = QUESTION_KEYWORDS[question_id]
    text_sources = [
        source
        for source in sources
        if source.kind != "provider" and not source.metadata.get("dataset")
    ]
    if not text_sources:
        return []
    source_ids = [source_id_text(source.id) for source in text_sources]
    source_by_id = {source_id_text(source.id): source for source in text_sources}
    with storage.open_db(root) as connection:
        matches = storage.search_source_pages(
            connection,
            source_ids,
            _fts_query(keywords),
            limit=max(32, MAX_CHUNKS_PER_QUESTION * len(source_ids)),
        )
        all_rows = storage.source_page_rows(connection, source_ids)
    # SQLite 的 unicode61 对 CJK 词界的处理随版本不同。先执行 FTS5（它会为典型英文
    # 年报标题缩小候选），随后并入同一来源的页以做逐字评分，保证不同 SQLite 版本和中文
    # 词界下也不会把页误判成“无证据”。
    present = {(str(row["source_id"]), str(row["locator"])) for row in matches}
    matches.extend(
        row for row in all_rows if (str(row["source_id"]), str(row["locator"])) not in present
    )
    candidates: list[tuple[int, EvidenceChunk]] = []
    for row in matches:
        source = source_by_id[str(row["source_id"])]
        score = _score(str(row["content"]), keywords)
        if not score:
            continue
        content, truncated = _window_around_keywords(str(row["content"]), keywords)
        candidates.append(
            (
                score,
                EvidenceChunk(
                    source_id=str(row["source_id"]),
                    locator=str(row["locator"]),
                    source_kind=source.kind,
                    support_eligible=research._is_core_eligible(source, as_of)
                    and source_policy.supports_eligible(source),
                    source_rank=source_policy.rank_for(source).value,
                    extraction_method=source_policy.extraction_for(source).value,
                    reconciliation_status=source_policy.reconciliation_for(source).value,
                    content=content,
                    dataset=None,
                    content_hash=str(row["content_hash"]),
                    truncated=truncated,
                ),
            )
        )
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
            score += QUESTION_DATASET_BONUS.get(question_id, {}).get(dataset or "", 0)
            if not score:
                continue
            excerpt, truncated = _window_around_keywords(rendered, keywords)
            stored_fields = node if not truncated else {"excerpt": excerpt}
            candidates.append(
                (
                    score,
                    EvidenceChunk(
                        source_id=source_id_text(source.id),
                        locator=locator,
                        source_kind=source.kind,
                        support_eligible=research._is_core_eligible(source, as_of)
                        and source_policy.supports_eligible(source),
                        source_rank=source_policy.rank_for(source).value,
                        extraction_method=source_policy.extraction_for(source).value,
                        reconciliation_status=source_policy.reconciliation_for(source).value,
                        fields=stored_fields,
                        period=str(
                            node.get("period_text")
                            or node.get("period")
                            or node.get("date_time_str")
                        )
                        if (
                            node.get("period_text")
                            or node.get("period")
                            or node.get("date_time_str")
                        )
                        is not None
                        else None,
                        currency=str(node.get("currency") or node.get("currency_code"))
                        if (node.get("currency") or node.get("currency_code"))
                        else None,
                        unit=str(node.get("unit")) if node.get("unit") else None,
                        accounting_standard=str(
                            node.get("accounting_standard") or node.get("accounting_standards")
                        )
                        if (node.get("accounting_standard") or node.get("accounting_standards"))
                        else None,
                        dataset=dataset,
                        content_hash=canonical_hash(node),
                        truncated=truncated,
                    ),
                )
            )
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
    candidates: list[tuple[int, EvidenceChunk, Source]],
    *,
    remaining_chars: int,
    used_source_chunks: dict[str, int],
    used_question_source_chunks: dict[str, int],
    max_chunks: int,
    as_of: Any,
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
        if (
            key in seen
            or used_source_chunks.get(chunk.source_id, 0) >= MAX_CHUNKS_PER_SOURCE
            or used_question_source_chunks.get(chunk.source_id, 0) >= MAX_CHUNKS_PER_QUESTION_SOURCE
        ):
            continue
        size = len(json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False, default=str))
        if size > remaining_chars:
            continue
        chosen.append(chunk)
        seen.add(key)
        used_source_chunks[chunk.source_id] = used_source_chunks.get(chunk.source_id, 0) + 1
        used_question_source_chunks[chunk.source_id] = (
            used_question_source_chunks.get(chunk.source_id, 0) + 1
        )
        remaining_chars -= size
        if len(chosen) >= max_chunks:
            break
    return chosen


def build_evidence_pack(
    root: Path,
    group_id: str,
    sources: list[Source],
    as_of: Any,
    *,
    indexed: bool = False,
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
    question_chunks: dict[str, list[EvidenceChunk]] = {
        question_id: [] for question_id in question_ids
    }
    used_source_chunks: dict[str, int] = {}
    used_question_source_chunks: dict[str, dict[str, int]] = {
        question_id: {} for question_id in question_ids
    }
    used_chars = 0
    for question_id in question_ids:
        selected = _select_question_chunks(
            candidates[question_id],
            remaining_chars=MAX_PACK_CHARS - used_chars,
            used_source_chunks=used_source_chunks,
            used_question_source_chunks=used_question_source_chunks[question_id],
            max_chunks=MIN_CHUNKS_PER_QUESTION,
            as_of=as_of,
        )
        question_chunks[question_id].extend(selected)
        used_chars += sum(
            len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False, default=str))
            for item in selected
        )
    # 第二轮在仍有预算时扩充到最多四段，保持题目顺序和候选排序稳定。
    for question_id in question_ids:
        if used_chars >= MAX_PACK_CHARS:
            break
        existing = {(item.source_id, item.locator) for item in question_chunks[question_id]}
        selected = _select_question_chunks(
            [
                item
                for item in candidates[question_id]
                if (item[1].source_id, item[1].locator) not in existing
            ],
            remaining_chars=MAX_PACK_CHARS - used_chars,
            used_source_chunks=used_source_chunks,
            used_question_source_chunks=used_question_source_chunks[question_id],
            max_chunks=MAX_CHUNKS_PER_QUESTION - len(question_chunks[question_id]),
            as_of=as_of,
        )
        question_chunks[question_id].extend(selected)
        used_chars += sum(
            len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False, default=str))
            for item in selected
        )
    chosen: list[EvidenceChunk] = []
    seen: set[tuple[str, str]] = set()
    for question_id in question_ids:
        for chunk in question_chunks[question_id]:
            if (chunk.source_id, chunk.locator) not in seen and len(chosen) < MAX_CHUNKS_PER_PACK:
                chosen.append(chunk)
                seen.add((chunk.source_id, chunk.locator))
    gaps = [
        f"{question_id} 没有命中归档证据片段"
        for question_id, items in question_chunks.items()
        if not items
    ]
    return EvidencePack(
        group_id=group_id,
        question_ids=list(question_ids),
        chunks=chosen,
        question_chunks=question_chunks,
        gaps=gaps,
    )


def build_evidence_packs(root: Path, sources: list[Source], as_of: Any) -> dict[str, EvidencePack]:
    _ensure_source_page_index(root, sources)
    return {
        key: build_evidence_pack(root, key, sources, as_of, indexed=True) for key in QUESTION_GROUPS
    }


def validate_review(
    review: DraftReview, draft: ResearchDraft, source_ids: set[str]
) -> list[research.ValidationIssue]:
    issues: list[research.ValidationIssue] = []
    if review.draft_hash != draft_hash(draft):
        issues.append(
            research.ValidationIssue(
                "semantic_review_stale", "review.draft_hash", "审查对应的草稿哈希与当前草稿不一致"
            )
        )
    for actual, expected, location in (
        (review.checked_questions, QUESTION_IDS, "review.checked_questions"),
        (review.checked_gates, GATE_IDS, "review.checked_gates"),
        (review.checked_moats, MOAT_IDS, "review.checked_moats"),
    ):
        if set(actual) != set(expected) or len(actual) != len(expected):
            issues.append(
                research.ValidationIssue(
                    "semantic_review_incomplete", location, "语义审查覆盖清单不完整或有重复"
                )
            )
    for index, item in enumerate(review.issues):
        for ref in item.evidence:
            if source_id_text(ref.source_id) not in source_ids:
                issues.append(
                    research.ValidationIssue(
                        "semantic_review_illegal_reference",
                        f"review.issues[{index}].evidence",
                        "审查引用不属于当前 run",
                    )
                )
    if review.status == "failed" or any(item.severity == "error" for item in review.issues):
        issues.append(
            research.ValidationIssue(
                "semantic_review_failed", "review", "当前草稿仍有语义审查 error"
            )
        )
    return issues


def review_prompt_payload(
    draft: ResearchDraft, packs: dict[str, EvidencePack], calculations: list[CalculationResult]
) -> dict[str, Any]:
    statements = [statement for answer in draft.answers for statement in answer.statements]
    statements.extend(gate.rationale for gate in draft.gates)
    statements.extend(moat.rationale for moat in draft.moats)
    statements.extend((draft.challenge.counterargument, draft.conclusion))
    statements.extend(item.statement for item in draft.review_explanations)
    cited = {
        (source_id_text(ref.source_id), ref.locator)
        for statement in statements
        for ref in [
            *statement.evidence,
            *(evidence for number in statement.numbers for evidence in number.evidence),
        ]
    }
    # 同一页可能被多个问题组引用。只删除完全相同的片段，不截去证据正文。
    compact_packs: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for key, pack in packs.items():
        compact_packs[key] = []
        for chunk in pack.chunks:
            if (chunk.source_id, chunk.locator) not in cited:
                continue
            payload = chunk.model_dump(mode="json")
            fingerprint = canonical_hash(payload)
            if fingerprint not in seen:
                compact_packs[key].append(payload)
                seen.add(fingerprint)
    return {
        "draft_hash": draft_hash(draft),
        "questions": QUESTION_TEXT,
        "moat_definitions": MOAT_DEFINITIONS,
        "moat_score_anchors": MOAT_SCORE_ANCHORS,
        "draft": draft.model_dump(mode="json"),
        "cited_evidence": compact_packs,
        "trusted_calculations": [item.model_dump(mode="json") for item in calculations],
    }
