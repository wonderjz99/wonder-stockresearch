"""阶段 1 的严格输入模型；不包含数据库或网络副作用。"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

QUESTION_IDS = tuple(f"Q{number:02d}" for number in range(1, 25))
GATE_IDS = ("G1", "G2", "G3", "G5", "G6", "G7")
MOAT_IDS = tuple(f"M{number}" for number in range(1, 7))

TICKER_PATTERN = re.compile(r"HK\.\d{5}|US\.[A-Z0-9.-]+")
REPORT_KINDS = ("standard", "addendum", "topic")
RUN_STATUSES = ("draft", "completed", "failed")


def validate_ticker(ticker: str) -> str:
    """校验股票代码；SYNTHETIC 只用于离线教学材料。"""
    if ticker == "SYNTHETIC" or TICKER_PATTERN.fullmatch(ticker):
        return ticker
    raise ValueError("ticker 必须是 HK.五位数字、US.大写标的或 SYNTHETIC")


class StrictModel(BaseModel):
    """拒绝未经规格允许的字段，防止模型输出悄悄扩张契约。"""

    model_config = ConfigDict(extra="forbid")


class EvidenceRef(StrictModel):
    source_id: UUID4
    locator: str = Field(min_length=1)
    relation: Literal["supports", "refutes", "context"]


class NumericFact(StrictModel):
    value: Decimal
    unit: str = Field(min_length=1)
    currency: str | None = None
    period: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(min_length=1)


class Statement(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["fact", "inference", "assumption"]
    text: str = Field(min_length=1)
    evidence: list[EvidenceRef]
    numbers: list[NumericFact]

    @model_validator(mode="after")
    def facts_have_evidence(self) -> Statement:
        if self.kind == "fact" and not self.evidence:
            raise ValueError("事实陈述必须至少有一条来源引用")
        return self


class Gap(StrictModel):
    missing: str = Field(min_length=1)
    where_to_find: str = Field(min_length=1)
    next_check: str = Field(min_length=1)
    impact: str = Field(min_length=1)


class Answer(StrictModel):
    question_id: str
    status: Literal["answered", "partial", "missing"]
    statements: list[Statement]
    gap: Gap | None = None

    @model_validator(mode="after")
    def incomplete_answers_describe_the_gap(self) -> Answer:
        if self.status in {"partial", "missing"} and self.gap is None:
            raise ValueError("partial 或 missing 问题必须提供 gap")
        return self


class Gate(StrictModel):
    gate_id: str
    result: Literal["pass", "fail", "insufficient"]
    rationale: Statement
    gap: Gap | None = None


class Moat(StrictModel):
    dimension_id: str
    score: int | None = Field(default=None, ge=0, le=10)
    confidence: Literal["low", "medium", "high"]
    rationale: Statement
    gap: Gap | None = None


class Challenge(StrictModel):
    counterargument: Statement
    channels_checked: list[str] = Field(min_length=1)
    search_scope: str = Field(min_length=1)
    refutation_found: bool
    overturning_evidence: str = Field(min_length=1)
    next_check: str = Field(min_length=1)


class Conflict(StrictModel):
    description: str = Field(min_length=1)
    source_ids: list[UUID4] = Field(min_length=1)
    critical: bool
    resolved: bool
    resolution: str | None = None

    @model_validator(mode="after")
    def resolved_conflicts_explain_the_resolution(self) -> Conflict:
        if self.resolved and not self.resolution:
            raise ValueError("已解决的冲突必须提供 resolution")
        return self


class ReviewExplanation(StrictModel):
    target_id: str = Field(min_length=1)
    statement: Statement


class ResearchDraft(StrictModel):
    schema_version: Literal[1]
    ticker: str
    as_of: datetime
    answers: list[Answer]
    gates: list[Gate]
    moats: list[Moat]
    challenge: Challenge
    conflicts: list[Conflict]
    conclusion: Statement
    review_explanations: list[ReviewExplanation]

    @field_validator("ticker")
    @classmethod
    def ticker_is_supported_or_synthetic(cls, ticker: str) -> str:
        return validate_ticker(ticker)

    @field_validator("as_of")
    @classmethod
    def as_of_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of 必须带时区")
        return value


class Source(StrictModel):
    id: UUID4
    ticker: str
    kind: Literal["primary", "provider", "secondary", "legacy"]
    title: str = Field(min_length=1)
    url: str | None = None
    endpoint: str | None = None
    origin_file_label: str | None = None
    published_at: datetime | None = None
    publication_precision: Literal["date", "datetime", "unknown"]
    publication_timezone: str | None = None
    retrieved_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_path: str = Field(min_length=1)
    text_path: str | None = None
    extraction_status: str = Field(min_length=1)
    metadata: dict[str, Any]

    @model_validator(mode="after")
    def source_has_origin_and_consistent_publication_metadata(self) -> Source:
        if not any((self.url, self.endpoint, self.origin_file_label)):
            raise ValueError("来源至少需要 url、endpoint 或 origin_file_label")
        if self.published_at is None and self.publication_precision != "unknown":
            raise ValueError("published_at 为空时 publication_precision 必须是 unknown")
        if self.published_at is not None and self.publication_precision == "unknown":
            raise ValueError("已知 published_at 时不能使用 unknown 精度")
        return self


class ResearchPacket(StrictModel):
    draft: ResearchDraft
    sources: list[Source]


def source_id_text(source_id: UUID | str) -> str:
    """为校验和报告提供稳定的小写 UUID 文本。"""
    return str(source_id)
