"""阶段 1 的严格输入模型；不包含数据库或网络副作用。"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
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


class SourceRequest(StrictModel):
    """source-add 的输入：恰好一个 url 或本地 file，程序自行计算哈希与获取时间。"""

    kind: Literal["primary", "provider", "secondary", "legacy"]
    title: str = Field(min_length=1)
    url: str | None = None
    file: str | None = None
    published_at: datetime | None = None
    publication_precision: Literal["date", "datetime", "unknown"] = "unknown"
    publication_timezone: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_origin(self) -> SourceRequest:
        if bool(self.url) == bool(self.file):
            raise ValueError("url 与 file 必须且只能提供一个")
        if self.published_at is None and self.publication_precision != "unknown":
            raise ValueError("published_at 为空时 publication_precision 必须是 unknown")
        if self.published_at is not None and self.publication_precision == "unknown":
            raise ValueError("已知 published_at 时不能使用 unknown 精度")
        return self


class ResearchPacket(StrictModel):
    draft: ResearchDraft
    sources: list[Source]


class ReviewIssue(StrictModel):
    category: Literal[
        "question_alignment",
        "evidence_support",
        "numeric_consistency",
        "cross_section_consistency",
        "scope_violation",
    ]
    target: str = Field(min_length=1)
    severity: Literal["error", "warning"]
    message: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    revision_instruction: str = Field(min_length=1)


class DraftReview(StrictModel):
    """可持久化的语义审查结论；不包含模型隐藏推理。"""

    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["passed", "failed"]
    checked_questions: list[str]
    checked_gates: list[str]
    checked_moats: list[str]
    issues: list[ReviewIssue]

    @model_validator(mode="after")
    def errors_imply_failed(self) -> DraftReview:
        has_error = any(issue.severity == "error" for issue in self.issues)
        if has_error and self.status != "failed":
            raise ValueError("存在 error 时 status 必须为 failed")
        if not has_error and self.status != "passed":
            raise ValueError("没有 error 时 status 必须为 passed")
        return self


class InteractiveQuestion(StrictModel):
    """交互 Agent 的一次问题；不代表正式 ResearchDraft。"""

    question: str = Field(min_length=1, max_length=12000)
    focus: Literal[
        "general",
        "challenge",
        "macro",
        "peer",
        "capacity_cycle",
        "inventory_cycle",
    ] = "general"
    context: Literal["latest_completed_report", "latest_run", "sources", "memos", "company_latest"] = (
        "latest_completed_report"
    )
    run_id: str | None = None
    allow_network: bool = True
    max_iterations: int = Field(default=10, ge=1, le=10)
    token_budget: int = Field(default=60000, ge=256, le=60000)
    input_token_limit: int = Field(default=16000, ge=1024, le=16000)
    output_token_limit: int = Field(default=4096, ge=64, le=8192)


class RoutePlan(StrictModel):
    """自然语言入口的受控路由结果；不直接代表可执行权限。"""

    intent: Literal[
        "fixed_research",
        "interactive_question",
        "memo_save",
        "session_followup",
        "status_query",
        "clarify",
        "unsupported",
    ]
    company_name: str | None = None
    ticker: str | None = None
    question: str | None = None
    focus: Literal[
        "general",
        "challenge",
        "macro",
        "peer",
        "capacity_cycle",
        "inventory_cycle",
    ] = "general"
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=1000)
    requires_confirmation: bool = True
    skill_names: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("ticker")
    @classmethod
    def ticker_is_valid_when_present(cls, ticker: str | None) -> str | None:
        if ticker is None:
            return None
        return validate_ticker(ticker)

    @field_validator("focus", mode="before")
    @classmethod
    def null_focus_means_general(cls, focus: str | None) -> str:
        """兼容模型把没有专项方向的请求显式写成 null。"""
        return "general" if focus is None else focus

    @field_validator("skill_names")
    @classmethod
    def selected_skills_are_unique(cls, names: list[str]) -> list[str]:
        cleaned = [name.strip() for name in names]
        if any(not name for name in cleaned):
            raise ValueError("skill_names 不能包含空名称")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("skill_names 不能重复")
        return cleaned


class SymbolCandidate(StrictModel):
    """自然语言标的消歧候选；不是研究证据，也不代表用户已选择。"""

    candidate_id: str = Field(min_length=1)
    ticker: str
    name: str = Field(min_length=1)
    market: Literal["HK", "US"]
    security_type: str = Field(min_length=1)
    source: Literal["local", "futu", "model_suggestion"]
    verified: bool

    @field_validator("ticker")
    @classmethod
    def ticker_is_supported(cls, ticker: str) -> str:
        return validate_ticker(ticker)


class InteractiveFollowup(StrictModel):
    question: str = Field(min_length=1, max_length=12000)


class InteractiveClaim(StrictModel):
    text: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class InteractiveReport(StrictModel):
    """Agent 的临时研究结果；不会自动进入正式研究报告。"""

    session_id: UUID4
    ticker: str
    user_question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    claims: list[InteractiveClaim] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    counterarguments: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggested_next_checks: list[str] = Field(default_factory=list)
    created_at: datetime

    @field_validator("ticker")
    @classmethod
    def ticker_is_supported_or_synthetic(cls, ticker: str) -> str:
        return validate_ticker(ticker)

    @field_validator("created_at")
    @classmethod
    def created_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at 必须带时区")
        return value


class AgentDecision(StrictModel):
    """DeepSeek 交互循环的有限 JSON 协议。"""

    type: Literal["tool_call", "final", "ask_user"]
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    answer: str | None = None
    claims: list[InteractiveClaim] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    counterarguments: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggested_next_checks: list[str] = Field(default_factory=list)
    question: str | None = None

    @model_validator(mode="after")
    def decision_shape_is_consistent(self) -> AgentDecision:
        if self.type == "tool_call" and not self.tool:
            raise ValueError("tool_call 必须提供 tool")
        if self.type == "final" and not self.answer:
            raise ValueError("final 必须提供 answer")
        if self.type == "ask_user" and not self.question:
            raise ValueError("ask_user 必须提供 question")
        return self


class MemoRequest(StrictModel):
    """用户明确选择保存到公司 Memo 的内容。"""

    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=30000)
    source_refs: list[EvidenceRef] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list, max_length=20)


LegacyTurnId = Annotated[str, Field(pattern=r"^legacy-[0-9a-f-]{36}$")]


class CompanyMemo(StrictModel):
    """公司级长期备忘录；不是正式报告，也不直接参与评分。"""

    memo_id: UUID4
    ticker: str
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_refs: list[EvidenceRef] = Field(min_length=1)
    created_from_session: UUID4
    created_from_turn: UUID4 | LegacyTurnId | None = None
    created_from_report_turn: UUID4 | LegacyTurnId | None = None
    tags: list[str] = Field(default_factory=list)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("ticker")
    @classmethod
    def ticker_is_supported_or_synthetic(cls, ticker: str) -> str:
        return validate_ticker(ticker)

    @model_validator(mode="after")
    def memo_times_have_timezone(self) -> CompanyMemo:
        for value in (self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Memo 时间必须带时区")
        return self


def source_id_text(source_id: UUID | str) -> str:
    """为校验和报告提供稳定的小写 UUID 文本。"""
    return str(source_id)
