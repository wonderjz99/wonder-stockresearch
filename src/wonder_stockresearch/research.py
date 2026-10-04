"""阶段 1 的纯业务校验与派生值计算。"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from . import disclosures, futu_fundamentals, market, progress, source_policy, sources, storage
from .models import (
    GATE_IDS,
    MOAT_IDS,
    QUESTION_IDS,
    DraftReview,
    EvidenceRef,
    ResearchDraft,
    ResearchPacket,
    Source,
    SourceRequest,
    Statement,
    source_id_text,
    validate_ticker,
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    location: str
    message: str
    severity: Literal["error", "warning"] = "error"

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "location": self.location,
            "message": self.message,
            "severity": self.severity,
        }


def derive_score_total(draft: ResearchDraft) -> int | None:
    """六项评分只有全部已评估时才相加；未知不伪装为零。"""
    scores = [moat.score for moat in draft.moats]
    if any(score is None for score in scores):
        return None
    return sum(score for score in scores if score is not None)


def derive_quality_status(draft: ResearchDraft) -> str:
    """给报告的保守质量标签，不将其写回用户输入。"""
    if (
        any(answer.status != "answered" for answer in draft.answers)
        or any(answer.status == "answered" and answer.gap is not None for answer in draft.answers)
        or any(gate.result == "insufficient" for gate in draft.gates)
        or any(moat.score is None for moat in draft.moats)
    ):
        return "evidence_incomplete"
    return "complete"


def _coverage_issues(
    supplied: Iterable[str], expected: tuple[str, ...], location: str
) -> list[ValidationIssue]:
    supplied_values = list(supplied)
    counts = Counter(supplied_values)
    actual = set(supplied_values)
    issues: list[ValidationIssue] = []
    if duplicates := sorted(value for value, count in counts.items() if count > 1):
        issues.append(
            ValidationIssue("duplicate_id", location, f"重复编号：{', '.join(duplicates)}")
        )
    if missing := sorted(set(expected) - actual):
        issues.append(ValidationIssue("missing_id", location, f"缺少编号：{', '.join(missing)}"))
    if unexpected := sorted(actual - set(expected)):
        issues.append(
            ValidationIssue("unexpected_id", location, f"不允许的编号：{', '.join(unexpected)}")
        )
    return issues


def _statement_references(statement: Statement, location: str) -> Iterable[tuple[EvidenceRef, str]]:
    for index, reference in enumerate(statement.evidence):
        yield reference, f"{location}.evidence[{index}]"
    for number_index, number in enumerate(statement.numbers):
        for evidence_index, reference in enumerate(number.evidence):
            yield reference, f"{location}.numbers[{number_index}].evidence[{evidence_index}]"


def _as_aware(value: datetime) -> datetime:
    """把可能缺少时区的时间按 UTC 处理，避免与带时区的 as_of 比较时报错。"""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def _parse_metadata_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return storage.parse_iso(value)
    except ValueError:
        return None


def _is_core_eligible(source: Source, as_of: datetime) -> bool:
    """核心结论只能由 as_of 时点已可用的一手/ provider 来源支撑。

    - primary：必须有已知发布日期且不晚于 as_of；只有日期时以当地日终为保守可用时点；
      发布日期未知只作背景。
    - provider（方案 4）：若有 metadata.available_from（报告期末/披露日）则按它判断，
      不晚于 as_of 即可支撑；否则按实时快照规则要求 observed_at 可解析且不晚于 as_of，
      且 observed_after_as_of 不为 true。
    - secondary / legacy：不独立支撑核心结论。
    """
    if source.kind not in {"primary", "provider"} or not source_policy.supports_eligible(source):
        return False
    published_at = _as_aware(source.published_at) if source.published_at is not None else None
    if published_at is not None and published_at > as_of:
        return False
    if source.kind == "primary":
        if published_at is None:
            return False
        if source.publication_precision == "date":
            end_of_day = published_at.replace(hour=23, minute=59, second=59, microsecond=999999)
            return as_of >= end_of_day
        return True
    # provider：方案 4 —— 有明确期间/披露日的已报告数据（available_from）可在 as_of 前支撑核心结论；
    # 实时快照（无 available_from）仍按 observed_at 规则，晚于 as_of 只能作 context。
    available_from = _parse_metadata_time(source.metadata.get("available_from"))
    if available_from is not None:
        return available_from <= as_of
    if source.metadata.get("observed_after_as_of") is True:
        return False
    observed_at = _parse_metadata_time(source.metadata.get("observed_at"))
    if observed_at is None:
        return False
    return observed_at <= as_of


def _core_support_status(
    statement: Statement, sources: dict[str, Source], as_of: datetime
) -> tuple[bool, bool]:
    """返回 (是否有 as_of 可用的 supports 引用, 是否存在不可用的一手/provider supports 引用)。"""
    has_eligible = False
    has_ineligible = False
    for reference, _ in _statement_references(statement, "statement"):
        if reference.relation != "supports":
            continue
        source = sources.get(source_id_text(reference.source_id))
        if source is None or source.kind not in {"primary", "provider"}:
            continue
        if _is_core_eligible(source, as_of):
            has_eligible = True
        else:
            has_ineligible = True
    return has_eligible, has_ineligible


def _core_support_issue(
    statement: Statement, sources: dict[str, Source], as_of: datetime, location: str
) -> ValidationIssue | None:
    """缺少可用核心支持时返回问题；区分“没有权威引用”和“有但 as_of 不可用”。"""
    has_eligible, has_ineligible = _core_support_status(statement, sources, as_of)
    if has_eligible:
        return None
    if has_ineligible:
        return ValidationIssue(
            "ineligible_as_of_support",
            location,
            "supporting 引用的 primary/provider 来源在 as_of 时点不可用"
            "（无已知发布日期、provider 缺 observed_at、或发布时间/观测时间晚于 as_of），只能作 context",
        )
    return ValidationIssue(
        "missing_authoritative_support",
        location,
        "需要 primary 或 provider 的 supports 引用",
    )


def _append_unknown_source_issues(
    issues: list[ValidationIssue],
    statement: Statement,
    location: str,
    sources: dict[str, Source],
) -> None:
    for reference, reference_location in _statement_references(statement, location):
        if source_id_text(reference.source_id) not in sources:
            issues.append(
                ValidationIssue(
                    "unknown_source",
                    reference_location,
                    f"引用的 source_id 不在 packet.sources 中：{reference.source_id}",
                )
            )


def validate_packet(packet: ResearchPacket) -> list[ValidationIssue]:
    """校验一个离线包的覆盖、引用和时间边界，不访问文件系统或网络。"""
    draft = packet.draft
    issues: list[ValidationIssue] = []
    issues.extend(_coverage_issues((item.question_id for item in draft.answers), QUESTION_IDS, "draft.answers"))
    issues.extend(_coverage_issues((item.gate_id for item in draft.gates), GATE_IDS, "draft.gates"))
    issues.extend(_coverage_issues((item.dimension_id for item in draft.moats), MOAT_IDS, "draft.moats"))

    source_ids = [source_id_text(source.id) for source in packet.sources]
    duplicate_sources = sorted(value for value, count in Counter(source_ids).items() if count > 1)
    if duplicate_sources:
        issues.append(
            ValidationIssue("duplicate_source", "sources", f"重复 source_id：{', '.join(duplicate_sources)}")
        )
    sources = {source_id_text(source.id): source for source in packet.sources}
    for index, source in enumerate(packet.sources):
        source_location = f"sources[{index}]"
        if source.ticker != draft.ticker:
            issues.append(
                ValidationIssue(
                    "source_ticker_mismatch",
                    source_location,
                    "来源 ticker 必须与草稿 ticker 相同",
                )
            )
        if source.published_at is not None and source.published_at > draft.as_of:
            issues.append(
                ValidationIssue(
                    "future_source",
                    source_location,
                    "发布日期晚于研究截止时点，不能作为本次证据",
                )
            )

    for answer_index, answer in enumerate(draft.answers):
        answer_location = f"draft.answers[{answer_index}]"
        if answer.status == "answered" and answer.gap is not None:
            issues.append(
                ValidationIssue(
                    "answered_with_gap",
                    answer_location,
                    "answered 表示该题无未解决证据缺口；存在 gap 时应标为 partial 或 missing",
                )
            )
        for statement_index, statement in enumerate(answer.statements):
            statement_location = f"{answer_location}.statements[{statement_index}]"
            _append_unknown_source_issues(issues, statement, statement_location, sources)
            if answer.status == "answered":
                issue = _core_support_issue(statement, sources, draft.as_of, statement_location)
                if issue is not None:
                    issues.append(issue)

    for gate_index, gate in enumerate(draft.gates):
        gate_location = f"draft.gates[{gate_index}].rationale"
        _append_unknown_source_issues(issues, gate.rationale, gate_location, sources)
        if gate.result in {"pass", "fail"}:
            issue = _core_support_issue(gate.rationale, sources, draft.as_of, gate_location)
            if issue is not None:
                issues.append(issue)

    for moat_index, moat in enumerate(draft.moats):
        moat_location = f"draft.moats[{moat_index}].rationale"
        _append_unknown_source_issues(issues, moat.rationale, moat_location, sources)
        if moat.score is not None:
            issue = _core_support_issue(moat.rationale, sources, draft.as_of, moat_location)
            if issue is not None:
                issues.append(issue)

    _append_unknown_source_issues(issues, draft.challenge.counterargument, "draft.challenge.counterargument", sources)
    _append_unknown_source_issues(issues, draft.conclusion, "draft.conclusion", sources)
    for conflict_index, conflict in enumerate(draft.conflicts):
        for source_index, source_id in enumerate(conflict.source_ids):
            if source_id_text(source_id) not in sources:
                issues.append(
                    ValidationIssue(
                        "unknown_source",
                        f"draft.conflicts[{conflict_index}].source_ids[{source_index}]",
                        f"冲突引用的 source_id 不在 packet.sources 中：{source_id}",
                    )
                )
        if conflict.critical and not conflict.resolved:
            issues.append(
                ValidationIssue(
                    "unresolved_critical_conflict",
                    f"draft.conflicts[{conflict_index}]",
                    "关键来源冲突未解决，不能形成有效研究包",
                )
            )
    for explanation_index, explanation in enumerate(draft.review_explanations):
        _append_unknown_source_issues(
            issues,
            explanation.statement,
            f"draft.review_explanations[{explanation_index}].statement",
            sources,
        )
    return issues


class ResearchError(Exception):
    """业务层错误；exit_code 2 表示校验/身份拒绝，1 表示 I/O 或持久化错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = 1,
        issues: Sequence[ValidationIssue] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.issues = list(issues) if issues else []
        super().__init__(message)


def _checked_ticker(ticker: str) -> str:
    try:
        return validate_ticker(ticker)
    except ValueError as error:
        raise ResearchError("invalid_ticker", str(error), exit_code=2) from error


def _require_run(connection: object, run_id: str) -> dict[str, object]:
    run = storage.get_run(connection, run_id)  # type: ignore[arg-type]
    if run is None:
        raise ResearchError("unknown_run", f"找不到 run：{run_id}", exit_code=2)
    return run


def _packet_from_run(connection: object, run: dict[str, object]) -> ResearchPacket:
    if not run["draft_json"]:
        raise ResearchError("empty_draft", "该 run 还没有保存草稿", exit_code=2)
    draft = ResearchDraft.model_validate_json(run["draft_json"])  # type: ignore[arg-type]
    rows = storage.get_run_sources(connection, run["id"])  # type: ignore[arg-type]
    sources = [Source.model_validate_json(row["source_json"]) for row in rows]
    return ResearchPacket(draft=draft, sources=sources)


def _archive_issues(root: Path, packet: ResearchPacket) -> list[ValidationIssue]:
    """只校验已归档到 data/stocks/ 的来源；离线元数据来源不受影响。"""
    issues: list[ValidationIssue] = []
    for index, source in enumerate(packet.sources):
        if not source.raw_path.startswith("data/stocks/"):
            continue
        location = f"sources[{index}]"
        path = root / source.raw_path
        if not path.is_file():
            issues.append(
                ValidationIssue("missing_archived_file", location, f"归档原文不存在：{source.raw_path}")
            )
        elif storage.sha256_file(path) != source.sha256:
            issues.append(
                ValidationIssue("source_hash_mismatch", location, f"归档原文哈希不符：{source.raw_path}")
            )
    return issues


def _source_row(source: Source) -> dict[str, object]:
    return {
        "id": source_id_text(source.id),
        "ticker": source.ticker,
        "kind": source.kind,
        "metadata_json": json.dumps(source.metadata, ensure_ascii=False, default=str),
        "sha256": source.sha256,
        "raw_path": source.raw_path,
        "text_path": source.text_path,
        "retrieved_at": storage.to_iso(source.retrieved_at),
        "source_json": source.model_dump_json(),
    }


def _derived_values(draft: ResearchDraft) -> dict[str, object]:
    return {
        "score_total": derive_score_total(draft),
        "quality_status": derive_quality_status(draft),
    }


def watch_add(root: Path, ticker: str, name: str | None = None) -> dict[str, object]:
    _checked_ticker(ticker)
    storage.initialize(root)
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.watch_stock(connection, ticker, name, storage.now_utc())
    return {"ticker": ticker, "name": name, "watched": True}


def watch_remove(root: Path, ticker: str) -> dict[str, object]:
    _checked_ticker(ticker)
    storage.initialize(root)
    with storage.project_lock(root), storage.open_db(root) as connection:
        if not storage.unwatch_stock(connection, ticker):
            raise ResearchError("unknown_stock", f"清单中没有该股票：{ticker}", exit_code=2)
    return {"ticker": ticker, "watched": False}


def watch_list(root: Path) -> dict[str, object]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        return {"stocks": storage.list_stocks(connection, watched_only=True)}


def start_run(
    root: Path,
    ticker: str,
    as_of: datetime | None = None,
    baseline_run_id: str | None = None,
) -> dict[str, object]:
    _checked_ticker(ticker)
    created_at = storage.now_utc()
    target_as_of = as_of or created_at
    if target_as_of.tzinfo is None or target_as_of.utcoffset() is None:
        raise ResearchError("invalid_as_of", "as_of 必须带时区", exit_code=2)
    storage.initialize(root)
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.ensure_stock(connection, ticker, created_at)
        if baseline_run_id is not None:
            baseline = _require_run(connection, baseline_run_id)
            if baseline["ticker"] != ticker:
                raise ResearchError("baseline_ticker_mismatch", "基准 run 属于其他股票", exit_code=2)
            if baseline["status"] != "completed":
                raise ResearchError("baseline_not_completed", "基准 run 尚未完成", exit_code=2)
            if storage.parse_iso(baseline["as_of"]) > target_as_of:
                raise ResearchError("baseline_after_run", "基准 run 时间晚于新研究", exit_code=2)
        run_id = str(uuid4())
        storage.insert_run(
            connection,
            run_id=run_id,
            ticker=ticker,
            as_of=target_as_of,
            kind="standard",
            parent_run_id=None,
            baseline_run_id=baseline_run_id,
            created_at=created_at,
        )
    return {
        "run_id": run_id,
        "ticker": ticker,
        "kind": "standard",
        "as_of": storage.to_iso(target_as_of),
        "baseline_run_id": baseline_run_id,
        "status": "draft",
        "created_at": storage.to_iso(created_at),
    }


def record_skill_selection(
    root: Path, run_id: str, selected_skills: list[dict[str, object]]
) -> dict[str, object]:
    """把实际加载的 Skill 元数据写入 run_steps，不保存正文或 references。"""
    if not selected_skills:
        return {"run_id": run_id, "skills": []}
    storage.initialize(root)
    with storage.open_db(root) as connection:
        _require_run(connection, run_id)
    input_hash = _input_hash({"skills": selected_skills})
    attempt = _begin_step(root, run_id, "skill_selection", "skill_registry", input_hash)
    _end_step(
        root,
        run_id,
        "skill_selection",
        attempt,
        "succeeded",
        output_ref="skill_selection",
        metadata={"skills": selected_skills},
    )
    return {"run_id": run_id, "skills": selected_skills}


def _effective_sources(
    connection: object, run_id: str, packet_sources: list[Source]
) -> list[Source]:
    """draft 的 sources 已归档到 run 时无需在 packet 里重复声明，校验时合并已有来源。"""
    merged: dict[str, Source] = {}
    for row in storage.get_run_sources(connection, run_id):  # type: ignore[arg-type]
        source = Source.model_validate_json(row["source_json"])
        merged[source_id_text(source.id)] = source
    for source in packet_sources:
        merged[source_id_text(source.id)] = source
    return list(merged.values())


def save_draft(
    root: Path, run_id: str, packet: ResearchPacket
) -> tuple[dict[str, object], list[ValidationIssue]]:
    storage.initialize(root)
    with storage.project_lock(root), storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        if run["status"] == "completed":
            raise ResearchError("run_completed", "已完成的研究不能覆盖草稿", exit_code=2)
        if packet.draft.ticker != run["ticker"]:
            raise ResearchError("ticker_mismatch", "草稿 ticker 与 run 不一致", exit_code=2)
        for source in packet.sources:
            if source.ticker != run["ticker"]:
                raise ResearchError("source_ticker_mismatch", "来源 ticker 与 run 不一致", exit_code=2)
        effective_sources = _effective_sources(connection, run_id, packet.sources)
        issues = validate_packet(ResearchPacket(draft=packet.draft, sources=effective_sources))
        connection.execute("BEGIN IMMEDIATE")
        try:
            storage.update_draft(connection, run_id, packet.draft.model_dump_json(), storage.now_utc())
            for source in packet.sources:
                storage.upsert_source(connection, _source_row(source))
                storage.link_run_source(connection, run_id, source_id_text(source.id))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    # save-draft 也允许一并导入官方结构化事实；提交后重新核对，不能只在 collect/source-add 时更新。
    _refresh_source_reconciliation(root, run_id)
    return {
        "run_id": run_id,
        "status": "draft",
        "saved": True,
        "source_count": len(effective_sources),
    }, issues


def validate_run(root: Path, run_id: str) -> list[ValidationIssue]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        if not run["draft_json"]:
            return [ValidationIssue("empty_draft", "draft", "该 run 还没有保存草稿")]
        packet = _packet_from_run(connection, run)
        return (
            list(validate_packet(packet))
            + _archive_issues(root, packet)
            + _semantic_review_issues(connection, run, packet)
            + _reconciliation_issues(connection, run, packet.draft)
        )


def _semantic_review_issues(
    connection: object, run: dict[str, object], packet: ResearchPacket
) -> list[ValidationIssue]:
    """schema v6 起，standard run 必须有与当前草稿匹配的 passing review。"""
    if run["kind"] != "standard" or int(run["schema_version"]) < 6:
        return []
    # 仓库内的虚构教学 packet 用于验证旧生命周期；它不代表可定稿的真实公司研究。
    if packet.sources and all(source.metadata.get("teaching") is True for source in packet.sources):
        return []
    row = storage.latest_draft_review(connection, str(run["id"]))  # type: ignore[arg-type]
    if row is None:
        return [ValidationIssue("semantic_review_missing", "review", "该草稿尚未完成语义审查")]
    from .fixed_quality import validate_review

    try:
        review = DraftReview.model_validate_json(row["review_json"])
    except ValidationError:
        return [ValidationIssue("semantic_review_failed", "review", "保存的语义审查结构无效")]
    return validate_review(
        review,
        packet.draft,
        {source_id_text(source.id) for source in packet.sources},
    )


def save_draft_review(
    root: Path,
    run_id: str,
    review: DraftReview,
    *,
    reviewer_type: str,
    provider: str | None,
    model: str | None,
    template_version: str,
) -> tuple[dict[str, object], list[ValidationIssue]]:
    """校验并保存模型或人工审查；不允许虚假通过、漏审或越界引用。"""
    storage.initialize(root)
    with storage.project_lock(root), storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        if run["status"] != "draft":
            raise ResearchError("invalid_status", "只有 draft 可以保存语义审查", exit_code=2)
        packet = _packet_from_run(connection, run)
        from .fixed_quality import validate_review

        validation = validate_review(
            review, packet.draft, {source_id_text(source.id) for source in packet.sources}
        )
        structural = [issue for issue in validation if issue.code != "semantic_review_failed"]
        if structural:
            raise ResearchError("invalid_semantic_review", "语义审查结果无效", exit_code=2, issues=structural)
        storage.insert_draft_review(
            connection,
            run_id=run_id,
            draft_hash=review.draft_hash,
            status=review.status,
            reviewer_type=reviewer_type,
            provider=provider,
            model=model,
            template_version=template_version,
            review_json=review.model_dump_json(),
            created_at=storage.now_utc(),
        )
    return {"run_id": run_id, "review_status": review.status, "draft_hash": review.draft_hash}, validation


def _verify_completed_artifacts(root: Path, run: dict[str, object]) -> dict[str, object]:
    if not run["artifact_hashes_json"]:
        raise ResearchError("artifact_missing", "completed run 缺少产物哈希", exit_code=1)
    hashes = json.loads(run["artifact_hashes_json"])  # type: ignore[arg-type]
    directory = storage.run_dir(
        root, run["ticker"], storage.parse_iso(run["created_at"]), run["id"]  # type: ignore[arg-type]
    )
    for name, expected in hashes.items():
        path = directory / name
        if not path.is_file() or storage.sha256_file(path) != expected:
            raise ResearchError("artifact_mismatch", f"产物缺失或被修改：{name}", exit_code=1)
    return {
        "run_id": run["id"],
        "status": "completed",
        "run_dir": storage.relative_path(root, directory),
        "artifacts": hashes,
    }


def finish_run(root: Path, run_id: str) -> dict[str, object]:
    from .reports import render_company, render_review

    storage.initialize(root)
    with storage.project_lock(root), storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        if run["status"] == "completed":
            return _verify_completed_artifacts(root, run)
        if run["status"] != "draft":
            raise ResearchError("invalid_status", "只有 draft 可以 finish；failed 请先 resume", exit_code=2)
        if not run["draft_json"]:
            raise ResearchError("empty_draft", "该 run 还没有保存草稿", exit_code=2)
        packet = _packet_from_run(connection, run)
        issues = (
            validate_packet(packet)
            + _archive_issues(root, packet)
            + _semantic_review_issues(connection, run, packet)
            + _reconciliation_issues(connection, run, packet.draft)
        )
        if issues:
            raise ResearchError(
                "validation_failed", "草稿未通过校验，未生成正式报告", exit_code=2, issues=issues
            )
        draft = packet.draft
        company_markdown = render_company(packet, archived=True, run_id=run_id)
        comparison: dict[str, object] | None = None
        baseline_id = run["baseline_run_id"]
        if baseline_id:
            baseline = _require_run(connection, str(baseline_id))
            if baseline["status"] != "completed":
                raise ResearchError("baseline_not_completed", "基准 run 尚未完成", exit_code=2)
            comparison = _build_comparison(connection, run, baseline, current_status="completed")
        snapshot = {
            "schema_version": draft.schema_version,
            "run_id": run_id,
            "ticker": run["ticker"],
            "kind": run["kind"],
            "as_of": draft.as_of.isoformat(),
            "status": "completed",
            "score_total": derive_score_total(draft),
            "quality_status": derive_quality_status(draft),
            "draft": json.loads(draft.model_dump_json()),
            "sources": [json.loads(source.model_dump_json()) for source in packet.sources],
        }
        quality_review_row = storage.latest_draft_review(connection, run_id)
        if quality_review_row is not None and int(run["schema_version"]) >= 6:
            quality_review = json.loads(quality_review_row["review_json"])
            snapshot["quality_review"] = quality_review
        if comparison is not None:
            snapshot["comparison"] = comparison
        directory = storage.run_dir(
            root, run["ticker"], storage.parse_iso(run["created_at"]), run_id  # type: ignore[arg-type]
        )
        try:
            storage.write_atomic(directory / "company.md", company_markdown)
            storage.write_atomic(
                directory / "research.json",
                json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            )
            if quality_review_row is not None and int(run["schema_version"]) >= 6:
                storage.write_atomic(
                    directory / "quality-review.json",
                    json.dumps(quality_review, ensure_ascii=False, indent=2) + "\n",
                )
            if comparison is not None:
                storage.write_atomic(directory / "review.md", render_review(comparison))
        except OSError as error:
            storage.mark_run_failed(
                connection,
                run_id,
                json.dumps({"code": "artifact_write_failed", "message": str(error)}, ensure_ascii=False),
                storage.now_utc(),
            )
            raise ResearchError("artifact_write_failed", f"写入产物失败：{error}") from error
        completed_at = storage.now_utc()
        hashes = {
            "company.md": storage.sha256_file(directory / "company.md"),
            "research.json": storage.sha256_file(directory / "research.json"),
        }
        if comparison is not None:
            hashes["review.md"] = storage.sha256_file(directory / "review.md")
        if quality_review_row is not None and int(run["schema_version"]) >= 6:
            hashes["quality-review.json"] = storage.sha256_file(directory / "quality-review.json")
        connection.execute("BEGIN IMMEDIATE")
        try:
            updated = storage.mark_run_completed(
                connection, run_id, completed_at, json.dumps(hashes, ensure_ascii=False)
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        if updated == 0:
            raise ResearchError("concurrent_update", "run 状态已被其他写入改变", exit_code=1)
        return {
            "run_id": run_id,
            "status": "completed",
            "run_dir": storage.relative_path(root, directory),
            "artifacts": hashes,
            **_derived_values(draft),
        }


def resume_run(root: Path, run_id: str) -> dict[str, object]:
    storage.initialize(root)
    with storage.project_lock(root), storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        if run["status"] == "completed":
            raise ResearchError("completed_cannot_resume", "已完成的研究不能 resume", exit_code=2)
        if run["status"] != "failed":
            raise ResearchError("not_failed", "只有 failed 的 run 可以 resume", exit_code=2)
        updated = storage.mark_run_resumed(connection, run_id, storage.now_utc())
        if updated == 0:
            raise ResearchError("concurrent_update", "run 状态已被其他写入改变", exit_code=1)
    return {"run_id": run_id, "status": "draft"}


def get_status(root: Path, run_id: str) -> dict[str, object]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        data: dict[str, object] = {
            key: run[key]
            for key in (
                "id",
                "ticker",
                "kind",
                "parent_run_id",
                "as_of",
                "baseline_run_id",
                "status",
                "created_at",
                "completed_at",
            )
        }
        if run["draft_json"]:
            draft = ResearchDraft.model_validate_json(run["draft_json"])  # type: ignore[arg-type]
            data.update(_derived_values(draft))
            data["answer_count"] = len(draft.answers)
        else:
            data["score_total"] = None
            data["quality_status"] = "draft"
            data["answer_count"] = 0
        data["source_count"] = len(storage.get_run_sources(connection, run_id))
        reconciliation = storage.list_source_conflicts(connection, run_id)
        data["source_reconciliation"] = {
            "matched": sum(row["status"] == "matched" for row in reconciliation),
            "provider_unverified": sum(row["status"] == "provider_unverified" for row in reconciliation),
            "conflicts": sum(row["status"] == "conflict" for row in reconciliation),
            "warning": "部分富途数值尚无同 run 官方结构化明细可核对"
            if any(row["status"] == "provider_unverified" for row in reconciliation)
            else None,
        }
        data["steps"] = storage.list_steps(connection, run_id)
        write_steps = [step for step in data["steps"] if step["step_key"] == "write_draft"]  # type: ignore[index]
        if write_steps:
            latest_metadata = write_steps[-1].get("metadata_json")  # type: ignore[union-attr]
            parsed_metadata = json.loads(latest_metadata) if latest_metadata else {}
            calls = int(parsed_metadata.get("model_calls") or len(parsed_metadata.get("model_attempts") or []))
            data["call_budget"] = {
                "used": calls,
                "limit": 14,
                "remaining": max(0, 14 - calls),
                "revisions": int(parsed_metadata.get("revisions") or 0),
            }
        else:
            data["call_budget"] = {"used": 0, "limit": 14, "remaining": 14, "revisions": 0}
        sections = storage.list_draft_sections(connection, run_id)
        data["draft_sections"] = [
            {
                "section_key": item["section_key"],
                "input_hash": item["input_hash"],
                "output_hash": item["output_hash"],
                "provider": item["provider"],
                "model": item["model"],
                "template_version": item["template_version"],
                "created_at": item["created_at"],
            }
            for item in sections
        ]
        review_row = storage.latest_draft_review(connection, run_id)
        if review_row is None:
            data["semantic_review"] = {"status": "missing"}
        else:
            review = DraftReview.model_validate_json(review_row["review_json"])
            current_hash = None
            if run["draft_json"]:
                from .fixed_quality import draft_hash
                current_hash = draft_hash(ResearchDraft.model_validate_json(run["draft_json"]))
            data["semantic_review"] = {
                "status": "stale" if current_hash != review.draft_hash else review.status,
                "draft_hash": review.draft_hash,
                "issue_count": len(review.issues),
                "error_count": sum(issue.severity == "error" for issue in review.issues),
                "warning_count": sum(issue.severity == "warning" for issue in review.issues),
                "reviewer_type": review_row["reviewer_type"],
            }
            data["draft_hash"] = current_hash
        if run["error_json"]:
            data["error"] = json.loads(run["error_json"])
    return data


def get_draft(root: Path, run_id: str) -> dict[str, object]:
    """读取当前保存的草稿，不生成报告、不改变 run。"""
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        if not run["draft_json"]:
            raise ResearchError("empty_draft", "该 run 尚无已保存草稿", exit_code=2)
        draft = ResearchDraft.model_validate_json(run["draft_json"])
    from .fixed_quality import draft_hash

    return {
        "run_id": run_id,
        "ticker": run["ticker"],
        "status": run["status"],
        "draft_hash": draft_hash(draft),
        "draft": draft.model_dump(mode="json"),
    }


def get_draft_review(root: Path, run_id: str) -> dict[str, object]:
    """读取最新结构化审查，不触发模型调用或保存。"""
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        row = storage.latest_draft_review(connection, run_id)
        if row is None:
            raise ResearchError("review_missing", "该 run 尚无已保存语义审查", exit_code=2)
        review = DraftReview.model_validate_json(row["review_json"])
    return {
        "run_id": run_id,
        "ticker": run["ticker"],
        "reviewer_type": row["reviewer_type"],
        "provider": row["provider"],
        "model": row["model"],
        "template_version": row["template_version"],
        "created_at": row["created_at"],
        "review": review.model_dump(mode="json"),
    }


def preview_draft(root: Path, run_id: str) -> dict[str, object]:
    """生成 data/tmp 下的非权威 Markdown 草稿预览，不改变 run。"""
    from .reports import render_company, render_draft_review

    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        packet = _packet_from_run(connection, run)
        review_row = storage.latest_draft_review(connection, run_id)
        review = DraftReview.model_validate_json(review_row["review_json"]) if review_row else None
    directory = root / "data" / "tmp" / "draft-previews" / run_id
    draft_path = directory / "draft.md"
    storage.write_atomic(
        draft_path,
        render_company(packet, preview=True, run_id=run_id),
    )
    artifacts: dict[str, str] = {"draft.md": storage.relative_path(root, draft_path)}
    if review is not None:
        review_path = directory / "quality-review.md"
        storage.write_atomic(review_path, render_draft_review(review, run_id=run_id))
        artifacts["quality-review.md"] = storage.relative_path(root, review_path)
    return {"run_id": run_id, "ticker": run["ticker"], "status": run["status"], "artifacts": artifacts}


def get_history(root: Path, ticker: str) -> dict[str, object]:
    _checked_ticker(ticker)
    storage.initialize(root)
    with storage.open_db(root) as connection:
        return {"ticker": ticker, "runs": storage.list_runs(connection, ticker)}


MAX_READ_CHARS = 20000
MAX_READ_LINES = 300
DEFAULT_READ_LINES = 120


def _register_source(
    root: Path,
    connection: object,
    run: dict[str, object],
    *,
    kind: str,
    title: str,
    data: bytes,
    extension: str,
    metadata: dict[str, object],
    url: str | None = None,
    endpoint: str | None = None,
    origin_file_label: str | None = None,
    published_at: datetime | None = None,
    publication_precision: str = "unknown",
    publication_timezone: str | None = None,
    content_type: str | None = None,
) -> tuple[Source, bool]:
    """归档字节并登记来源；返回 (Source, 是否命中同哈希去重)。调用方须持锁。"""
    ticker = str(run["ticker"])
    sha256 = hashlib.sha256(data).hexdigest()
    existing = storage.find_source_by_hash(connection, ticker, sha256)  # type: ignore[arg-type]
    if existing is not None:
        existing_source = Source.model_validate_json(existing["source_json"])
        raw_path = existing_source.raw_path
        text_path = existing_source.text_path
        extraction_status = existing_source.extraction_status
    else:
        extracted_text, extraction_status = sources.extract_text(data, extension, content_type)
        raw_path = f"data/stocks/{ticker}/sources/{sha256}.{extension}"
        storage.write_bytes_atomic(root / raw_path, data)
        text_path = None
        if extraction_status == "available":
            text_path = f"data/stocks/{ticker}/sources/{sha256}.txt"
            storage.write_atomic(root / text_path, extracted_text)
    source = Source(
        id=uuid4(),
        ticker=ticker,
        kind=kind,
        title=title,
        url=url,
        endpoint=endpoint,
        origin_file_label=origin_file_label,
        published_at=published_at,
        publication_precision=publication_precision,
        publication_timezone=publication_timezone,
        retrieved_at=storage.now_utc(),
        sha256=sha256,
        raw_path=raw_path,
        text_path=text_path,
        extraction_status=extraction_status,
        metadata=source_policy.normalized_metadata(
            kind,
            metadata,
            extension=extension,
            extraction_status=extraction_status,
        ),
    )
    storage.upsert_source(connection, _source_row(source))  # type: ignore[arg-type]
    storage.link_run_source(connection, run["id"], source_id_text(source.id))  # type: ignore[arg-type]
    return source, existing is not None


def source_add(root: Path, run_id: str, request: SourceRequest) -> dict[str, object]:
    """先取得字节（不持锁），再登记来源；哈希与获取时间由程序计算。"""
    storage.initialize(root)
    acquired = sources.acquire(request)
    with storage.project_lock(root), storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        if run["status"] == "completed":
            raise ResearchError("run_completed", "已完成的研究不能新增来源", exit_code=2)
        source, deduplicated = _register_source(
            root,
            connection,
            run,
            kind=request.kind,
            title=request.title,
            data=acquired.data,
            extension=acquired.extension,
            metadata=dict(request.metadata),
            url=acquired.final_url if acquired.origin_label is None else None,
            origin_file_label=acquired.origin_label,
            published_at=request.published_at,
            publication_precision=request.publication_precision,
            publication_timezone=request.publication_timezone,
            content_type=acquired.content_type,
        )
    _refresh_source_reconciliation(root, run_id)
    return {
        "source_id": source_id_text(source.id),
        "run_id": run_id,
        "sha256": source.sha256,
        "raw_path": source.raw_path,
        "text_path": source.text_path,
        "extraction_status": source.extraction_status,
        "deduplicated": deduplicated,
        "retrieved_at": storage.to_iso(source.retrieved_at),
    }


def _source_summary(row: dict[str, object]) -> dict[str, object]:
    source = Source.model_validate_json(row["source_json"])
    return {
        "source_id": row["id"],
        "kind": row["kind"],
        "title": source.title,
        "origin": source.url or source.endpoint or source.origin_file_label,
        "published_at": source.published_at.isoformat() if source.published_at else None,
        "publication_precision": source.publication_precision,
        "retrieved_at": row["retrieved_at"],
        "sha256": row["sha256"],
        "raw_path": row["raw_path"],
        "text_path": row["text_path"],
        "extraction_status": source.extraction_status,
        "metadata": source.metadata,
    }


def list_sources(root: Path, run_id: str) -> dict[str, object]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        rows = storage.get_run_sources(connection, run_id)
    return {
        "run_id": run_id,
        "ticker": run["ticker"],
        "sources": [_source_summary(row) for row in rows],
    }


def _refresh_source_reconciliation(root: Path, run_id: str) -> list[dict[str, object]]:
    """以同一 run 的官方结构化明细复核富途数值，记录而不猜测 PDF 表格。"""
    with storage.project_lock(root), storage.open_db(root) as connection:
        _require_run(connection, run_id)
        sources_for_run = [
            Source.model_validate_json(row["source_json"])
            for row in storage.get_run_sources(connection, run_id)
        ]
        reconciled = source_policy.reconcile_sources(root, sources_for_run)
        by_provider: dict[str, list[source_policy.SourceConflict]] = {}
        for item in reconciled:
            by_provider.setdefault(item.provider_source_id, []).append(item)
        for source in sources_for_run:
            values = by_provider.get(source_id_text(source.id))
            if not values:
                continue
            statuses = {item.status for item in values}
            if source_policy.ReconciliationStatus.CONFLICT in statuses:
                status = source_policy.ReconciliationStatus.CONFLICT
            elif statuses == {source_policy.ReconciliationStatus.MATCHED}:
                status = source_policy.ReconciliationStatus.MATCHED
            else:
                status = source_policy.ReconciliationStatus.PROVIDER_UNVERIFIED
            source.metadata["reconciliation_status"] = status.value
            source.metadata["reconciliation_fact_count"] = len(values)
            storage.upsert_source(connection, _source_row(source))
        storage.replace_source_conflicts(
            connection,
            run_id,
            [item.to_storage_row() for item in reconciled],
            created_at=storage.now_utc(),
        )
    return [item.to_storage_row() for item in reconciled]


def _reconciliation_issues(
    connection: object, run: dict[str, object], draft: ResearchDraft
) -> list[ValidationIssue]:
    if int(run["schema_version"]) < 8:
        return []
    conflicts = storage.list_source_conflicts(connection, str(run["id"]))  # type: ignore[arg-type]
    resolved_pairs = {
        frozenset(source_id_text(source_id) for source_id in item.source_ids)
        for item in draft.conflicts
        if item.resolved
    }
    return [
        ValidationIssue(
            "unresolved_source_conflict",
            f"source_conflicts[{row['id']}]",
            f"富途与官方披露的 {row['metric']}（{row['period'] or '期间未知'}）不一致；官方值优先，未解决前不能 finish",
        )
        for row in conflicts
        if row["status"] == source_policy.ReconciliationStatus.CONFLICT.value
        and frozenset(
            source_id for source_id in (row["provider_source_id"], row["official_source_id"]) if source_id
        ) not in resolved_pairs
    ]


def _read_page(text: str, page: int) -> dict[str, object]:
    pages = text.split("\f")
    if page < 1 or page > len(pages):
        raise ResearchError("page_out_of_range", f"页码超出范围（共 {len(pages)} 页）", exit_code=2)
    content = pages[page - 1]
    truncated = len(content) > MAX_READ_CHARS
    if truncated:
        content = content[:MAX_READ_CHARS]
    return {
        "locator": f"page {page}",
        "page": page,
        "total_pages": len(pages),
        "text": content,
        "truncated": truncated,
        "next": {"page": page + 1} if page < len(pages) else None,
    }


def _read_lines(text: str, start_line: int, limit_lines: int) -> dict[str, object]:
    if start_line < 1:
        raise ResearchError("invalid_start_line", "start-line 必须不小于 1", exit_code=2)
    limit = max(1, min(limit_lines, MAX_READ_LINES))
    lines = text.splitlines()
    selected: list[str] = []
    characters = 0
    cursor = start_line - 1
    while cursor < len(lines) and len(selected) < limit:
        line = lines[cursor]
        selected.append(line)
        characters += len(line) + 1
        cursor += 1
        if characters >= MAX_READ_CHARS:
            break
    truncated = cursor < len(lines)
    body = "\n".join(selected)
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS]
        truncated = True
    return {
        "locator": f"lines {start_line}-{cursor}",
        "start_line": start_line,
        "end_line": cursor,
        "total_lines": len(lines),
        "text": body,
        "truncated": truncated,
        "next": {"start_line": cursor + 1} if truncated else None,
    }


def read_source(
    root: Path,
    run_id: str,
    source_id: str,
    *,
    page: int | None = None,
    start_line: int | None = None,
    limit_lines: int | None = None,
) -> dict[str, object]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        row = storage.get_source(connection, source_id)
        if (
            row is None
            or row["ticker"] != run["ticker"]
            or not storage.is_source_linked(connection, run_id, source_id)
        ):
            raise ResearchError("unknown_source", "该来源不属于此 run", exit_code=2)
    text_path = row["text_path"]
    if not text_path:
        raise ResearchError("extraction_unavailable", "该来源没有可用文本", exit_code=1)
    path = root / str(text_path)
    if not path.is_file():
        raise ResearchError("text_missing", "提取文本文件不存在，需要重新导入", exit_code=1)
    text = path.read_text(encoding="utf-8")
    if page is not None:
        return _read_page(text, page)
    return _read_lines(text, start_line or 1, limit_lines or DEFAULT_READ_LINES)


BLOCKED_PROVIDER_CODES = {"opend_unavailable", "futu_sdk_missing", "sec_user_agent_missing"}


def _input_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _begin_step(
    root: Path,
    run_id: str,
    step_key: str,
    provider: str,
    input_hash: str,
    *,
    model: str | None = None,
) -> int:
    with storage.project_lock(root), storage.open_db(root) as connection:
        attempt = storage.next_step_attempt(connection, run_id, step_key)
        storage.insert_step(
            connection,
            run_id,
            step_key,
            attempt,
            status="running",
            provider=provider,
            input_hash=input_hash,
            model=model,
            started_at=storage.now_utc(),
        )
    return attempt


def _end_step(
    root: Path,
    run_id: str,
    step_key: str,
    attempt: int,
    status: str,
    *,
    output_ref: str | None = None,
    error: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    error_json = json.dumps(error, ensure_ascii=False) if error else None
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.finish_step(
            connection,
            run_id,
            step_key,
            attempt,
            status=status,
            output_ref=output_ref,
            error_json=error_json,
            metadata_json=metadata_json,
            finished_at=storage.now_utc(),
        )


def _error_payload(error: BaseException) -> dict[str, object]:
    return {
        "code": getattr(error, "code", type(error).__name__),
        "message": str(error),
        "blocked": bool(getattr(error, "blocked", False)),
    }


def _step_status(error: BaseException) -> str:
    code = getattr(error, "code", "")
    if bool(getattr(error, "blocked", False)) or code in BLOCKED_PROVIDER_CODES:
        return "blocked"
    return "failed"


def _collect_market(
    root: Path, run: dict[str, object], provider: market.QuoteProvider
) -> list[dict[str, object]]:
    run_id = str(run["id"])
    ticker = str(run["ticker"])
    # 年度财务只由 fundamentals.statements 采集，避免对同一富途接口重复请求。
    tasks = (("market_snapshot", f"{ticker} 行情快照", provider.market_snapshot),)
    results: list[dict[str, object]] = []
    for step_key, title, call in tasks:
        input_hash = _input_hash({"ticker": ticker, "step": step_key, "provider": provider.name})
        attempt = _begin_step(root, run_id, step_key, provider.name, input_hash)
        try:
            payload = call(ticker)
            observed_at = storage.to_iso(storage.now_utc())
            body = json.dumps(
                {"provider": provider.name, "observed_at": observed_at, **payload},
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
            with storage.project_lock(root), storage.open_db(root) as connection:
                current = _require_run(connection, run_id)
                if current["status"] == "completed":
                    raise ResearchError("run_completed", "run 已完成，停止登记", exit_code=2)
                source, deduplicated = _register_source(
                    root,
                    connection,
                    current,
                    kind="provider",
                    title=title,
                    data=body,
                    extension="json",
                    endpoint=payload.get("endpoint"),
                    metadata={
                        "provider": provider.name,
                        "endpoint": payload.get("endpoint"),
                        "evidence_class": "snapshot",
                        "observed_at": observed_at,
                        "observed_after_as_of": storage.parse_iso(observed_at)
                        > storage.parse_iso(current["as_of"]),
                        "payload_key": step_key,
                    },
                )
            _end_step(
                root,
                run_id,
                step_key,
                attempt,
                "succeeded",
                output_ref=f"source:{source_id_text(source.id)}",
            )
            results.append(
                {
                    "step_key": step_key,
                    "status": "succeeded",
                    "source_id": source_id_text(source.id),
                    "observed_at": observed_at,
                    "deduplicated": deduplicated,
                }
            )
        except sources.SourceError as error:
            status = _step_status(error)
            error_payload = _error_payload(error)
            _end_step(root, run_id, step_key, attempt, status, error=error_payload)
            results.append({"step_key": step_key, "status": status, "error": error_payload})
        except ResearchError as error:
            error_payload = _error_payload(error)
            _end_step(root, run_id, step_key, attempt, "failed", error=error_payload)
            results.append({"step_key": step_key, "status": "failed", "error": error_payload})
    return results


def _collect_filings(
    root: Path, run: dict[str, object], provider: disclosures.DisclosureProvider
) -> list[dict[str, object]]:
    run_id = str(run["id"])
    ticker = str(run["ticker"])
    step_key = "filings"
    input_hash = _input_hash({"ticker": ticker, "step": step_key, "provider": provider.name})
    attempt = _begin_step(root, run_id, step_key, provider.name, input_hash)
    try:
        candidates = provider.list_filings(ticker)
    except sources.SourceError as error:
        status = _step_status(error)
        error_payload = _error_payload(error)
        _end_step(root, run_id, step_key, attempt, status, error=error_payload)
        return [{"step_key": step_key, "status": status, "error": error_payload}]

    index_body = json.dumps(
        {
            "provider": provider.name,
            "ticker": ticker,
            "candidate_count": len(candidates),
            "candidates": [candidate.to_dict() for candidate in candidates],
        },
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    with storage.project_lock(root), storage.open_db(root) as connection:
        current = _require_run(connection, run_id)
        if current["status"] == "completed":
            raise ResearchError("run_completed", "run 已完成，停止登记", exit_code=2)
        index_source, _ = _register_source(
            root,
            connection,
            current,
            kind="secondary",
            title=f"{ticker} 公告索引（{provider.name}）",
            data=index_body,
            extension="json",
            endpoint=provider.name,
            metadata={
                "provider": provider.name,
                "role": "filing_index",
                "candidate_count": len(candidates),
            },
        )
    index_ref = f"source:{source_id_text(index_source.id)}"

    if not candidates:
        _end_step(root, run_id, step_key, attempt, "succeeded", output_ref=index_ref)
        return [
            {
                "step_key": step_key,
                "status": "succeeded",
                "candidate_count": 0,
                "source_id": source_id_text(index_source.id),
            }
        ]

    selected = disclosures.select_filings(candidates)
    downloaded: list[str] = []
    failures: list[dict[str, object]] = []
    for item in selected:
        request = SourceRequest(
            kind="primary",
            title=str(item["title"]),
            url=str(item["url"]),
            published_at=item["published_at"],
            publication_precision="datetime" if item["published_at"] else "unknown",
            publication_timezone=item.get("timezone") or "Asia/Hong_Kong",
            metadata={
                "provider": provider.name,
                "role": item["role"],
                "category": item["category"],
            },
        )
        try:
            added = source_add(root, run_id, request)
            downloaded.append(str(added["source_id"]))
        except (sources.SourceError, ResearchError) as error:
            failures.append(
                {
                    "url": str(item["url"]),
                    "code": getattr(error, "code", type(error).__name__),
                    "message": str(error),
                }
            )
    status = "failed" if failures and not downloaded else "succeeded"
    error_payload = {"download_failures": failures} if failures else None
    _end_step(root, run_id, step_key, attempt, status, output_ref=index_ref, error=error_payload)
    result: dict[str, object] = {
        "step_key": step_key,
        "status": status,
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "downloaded": downloaded,
        "download_failures": failures,
        "source_id": source_id_text(index_source.id),
    }
    if status == "failed":
        result["error"] = {
            "code": "filings_download_failed",
            "message": "所有候选文件下载失败",
            "blocked": False,
        }
    return [result]


FUNDAMENTAL_TASKS = (
    ("fundamentals_company", ("profile", "executives", "plates", "shareholders")),
    ("fundamentals_financials", ("statements", "operational_efficiency")),
    ("fundamentals_revenue", ("revenue_breakdown",)),
    ("fundamentals_capital", ("dividends", "buybacks")),
)


def _collect_fundamentals(
    root: Path, run: dict[str, object], provider: futu_fundamentals.FundamentalsProvider
) -> list[dict[str, object]]:
    run_id = str(run["id"])
    ticker = str(run["ticker"])
    results: list[dict[str, object]] = []
    for step_key, datasets in FUNDAMENTAL_TASKS:
        input_hash = _input_hash({"ticker": ticker, "step": step_key, "provider": provider.name})
        attempt = _begin_step(root, run_id, step_key, provider.name, input_hash)
        source_ids: list[str] = []
        failures: list[dict[str, object]] = []
        observed_at = storage.to_iso(storage.now_utc())
        for dataset in datasets:
            try:
                payload = provider.fetch(dataset, ticker)
                body = json.dumps(
                    {"provider": provider.name, "dataset": dataset, "observed_at": observed_at, **payload},
                    ensure_ascii=False,
                    default=str,
                ).encode("utf-8")
                with storage.project_lock(root), storage.open_db(root) as connection:
                    current = _require_run(connection, run_id)
                    if current["status"] == "completed":
                        raise ResearchError("run_completed", "run 已完成，停止登记", exit_code=2)
                    source, _ = _register_source(
                        root,
                        connection,
                        current,
                        kind="provider",
                        title=f"{ticker} 基本面-{dataset}",
                        data=body,
                        extension="json",
                        endpoint=f"futu.{dataset}",
                        metadata={
                            "provider": provider.name,
                            "dataset": dataset,
                            "evidence_class": payload.get("evidence_class"),
                            "available_from": payload.get("available_from"),
                            "currency": payload.get("currency"),
                            "accounting_standard": payload.get("accounting_standard"),
                            "observed_at": observed_at,
                            "observed_after_as_of": storage.parse_iso(observed_at)
                            > storage.parse_iso(current["as_of"]),
                        },
                    )
                source_ids.append(source_id_text(source.id))
            except (sources.SourceError, ResearchError) as error:
                failures.append(_error_payload(error))
        error_payload = {"failures": failures} if failures else None
        if source_ids:
            _end_step(
                root, run_id, step_key, attempt, "succeeded",
                output_ref=f"source:{source_ids[0]}", error=error_payload,
            )
            results.append(
                {
                    "step_key": step_key,
                    "status": "succeeded",
                    "datasets": list(datasets),
                    "source_ids": source_ids,
                    "failures": failures,
                }
            )
        else:
            status = "blocked" if any(bool(f.get("blocked")) for f in failures) else "failed"
            _end_step(root, run_id, step_key, attempt, status, error=error_payload)
            results.append(
                {
                    "step_key": step_key,
                    "status": status,
                    "datasets": list(datasets),
                    "source_ids": [],
                    "failures": failures,
                    "error": error_payload,
                }
            )
    return results


def collect(
    root: Path,
    run_id: str,
    kind: str = "all",
    *,
    market_provider: market.QuoteProvider | None = None,
    disclosure_provider: disclosures.DisclosureProvider | None = None,
    fundamentals_provider: futu_fundamentals.FundamentalsProvider | None = None,
    on_progress: progress.ProgressCallback | None = None,
) -> tuple[dict[str, object], list[ValidationIssue]]:
    """采集行情/财务/公告，写入 run_steps 与来源；不生成评分、不 finish。"""
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
    if run["status"] == "completed":
        raise ResearchError("run_completed", "已完成的研究不能继续 collect", exit_code=2)
    ticker = str(run["ticker"])
    results: list[dict[str, object]] = []
    progress.emit(
        on_progress,
        "collect",
        "started",
        f"开始采集 {ticker}（{kind}）",
        run_id=run_id,
    )

    if kind in {"all", "market"}:
        progress.emit(on_progress, "collect.market", "started", "正在采集行情快照", run_id=run_id)
        provider = market_provider
        if provider is None:
            try:
                provider = market.make_provider()
            except sources.SourceError as error:
                payload = _error_payload(error)
                for step_key in ("market_snapshot",):
                    attempt = _begin_step(
                        root, run_id, step_key, "futu", _input_hash({"ticker": ticker, "step": step_key})
                    )
                    _end_step(root, run_id, step_key, attempt, "blocked", error=payload)
                    results.append({"step_key": step_key, "status": "blocked", "error": payload})
                provider = None
        if provider is not None:
            try:
                results.extend(_collect_market(root, run, provider))
            finally:
                provider.close()
        progress.emit(on_progress, "collect.market", "completed", "行情快照采集完成", run_id=run_id)

    if kind in {"all", "fundamentals"}:
        progress.emit(
            on_progress,
            "collect.fundamentals",
            "started",
            "正在采集基本面数据",
            run_id=run_id,
        )
        provider = fundamentals_provider
        if provider is None:
            try:
                provider = futu_fundamentals.make_provider()
            except sources.SourceError as error:
                payload = _error_payload(error)
                for step_key, _datasets in FUNDAMENTAL_TASKS:
                    attempt = _begin_step(
                        root, run_id, step_key, "futu", _input_hash({"ticker": ticker, "step": step_key})
                    )
                    _end_step(root, run_id, step_key, attempt, "blocked", error=payload)
                    results.append({"step_key": step_key, "status": "blocked", "error": payload})
                provider = None
        if provider is not None:
            try:
                results.extend(_collect_fundamentals(root, run, provider))
            finally:
                provider.close()
        progress.emit(
            on_progress,
            "collect.fundamentals",
            "completed",
            "基本面数据采集完成",
            run_id=run_id,
        )

    if kind in {"all", "filings"}:
        progress.emit(on_progress, "collect.filings", "started", "正在发现和归档公告", run_id=run_id)
        provider = disclosure_provider
        if provider is None:
            try:
                provider = disclosures.make_provider(ticker)
            except sources.SourceError as error:
                payload = _error_payload(error)
                attempt = _begin_step(
                    root, run_id, "filings", "disclosures", _input_hash({"ticker": ticker, "step": "filings"})
                )
                _end_step(root, run_id, "filings", attempt, "blocked", error=payload)
                results.append({"step_key": "filings", "status": "blocked", "error": payload})
                provider = None
        if provider is not None:
            try:
                results.extend(_collect_filings(root, run, provider))
            finally:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
        progress.emit(on_progress, "collect.filings", "completed", "公告发现和归档完成", run_id=run_id)

    issues: list[ValidationIssue] = []
    for result in results:
        status = result.get("status")
        error = result.get("error")
        if status in {"failed", "blocked"} and isinstance(error, dict):
            issues.append(
                ValidationIssue(
                    f"collect_{status}",
                    f"collect.{result['step_key']}",
                    f"{result['step_key']} {status}：{error.get('message', '')}",
                    severity="warning" if status == "blocked" else "error",
                )
            )
        for failure in result.get("download_failures", []) or []:
            issues.append(
                ValidationIssue(
                    "collect_download_failed",
                    "collect.filings",
                    f"下载失败 {failure.get('url')}：{failure.get('message')}",
                    severity="warning",
                )
            )
        for failure in result.get("failures", []) or []:
            issues.append(
                ValidationIssue(
                    "collect_dataset_failed",
                    f"collect.{result['step_key']}",
                    f"{result['step_key']} 数据集失败：{failure.get('message', '')}",
                    severity="warning" if failure.get("blocked") else "error",
                )
            )

    succeeded = sum(1 for result in results if result.get("status") == "succeeded")
    failed = sum(1 for result in results if result.get("status") == "failed")
    blocked = sum(1 for result in results if result.get("status") == "blocked")
    data: dict[str, object] = {
        "run_id": run_id,
        "ticker": ticker,
        "kind": kind,
        "steps": results,
        "succeeded": succeeded,
        "failed": failed,
        "blocked": blocked,
    }
    # 公告通常在 collect 的末尾才进入 run；所有子任务完成后再做一次可复现核对。
    if results:
        _refresh_source_reconciliation(root, run_id)
    progress.emit(
        on_progress,
        "collect",
        "succeeded" if succeeded else "blocked",
        f"采集结束：成功 {succeeded}，失败 {failed}，阻塞 {blocked}",
        run_id=run_id,
    )
    return data, issues


def _score_diff(baseline: int | None, current: int | None) -> int | None:
    """缺任一分值时差值不计算，避免把未知当作零。"""
    if baseline is None or current is None:
        return None
    return current - baseline


def _draft_referenced_source_ids(draft: ResearchDraft) -> set[str]:
    referenced: set[str] = set()

    def collect(statement: Statement) -> None:
        for reference, _ in _statement_references(statement, "statement"):
            referenced.add(source_id_text(reference.source_id))

    for answer in draft.answers:
        for statement in answer.statements:
            collect(statement)
    for gate in draft.gates:
        collect(gate.rationale)
    for moat in draft.moats:
        collect(moat.rationale)
    collect(draft.challenge.counterargument)
    collect(draft.conclusion)
    for conflict in draft.conflicts:
        for source_id in conflict.source_ids:
            referenced.add(source_id_text(source_id))
    for explanation in draft.review_explanations:
        collect(explanation.statement)
    return referenced


def _build_comparison(
    connection: object,
    run: dict[str, object],
    baseline: dict[str, object],
    *,
    current_status: str | None = None,
) -> dict[str, object]:
    """纯计算 baseline 与当前 run 的结构差异；不写文件、不改数据库。"""
    current_packet = _packet_from_run(connection, run)
    baseline_packet = _packet_from_run(connection, baseline)
    current_draft = current_packet.draft
    baseline_draft = baseline_packet.draft
    comparable = (
        current_draft.schema_version == baseline_draft.schema_version
        and current_draft.ticker == baseline_draft.ticker
    )

    baseline_answers = {answer.question_id: answer for answer in baseline_draft.answers}
    current_answers = {answer.question_id: answer for answer in current_draft.answers}
    answers: list[dict[str, object]] = []
    for question_id in QUESTION_IDS:
        previous = baseline_answers.get(question_id)
        latest = current_answers.get(question_id)
        previous_status = previous.status if previous else None
        latest_status = latest.status if latest else None
        answers.append(
            {
                "question_id": question_id,
                "baseline_status": previous_status,
                "current_status": latest_status,
                "changed": previous_status != latest_status,
            }
        )

    baseline_gates = {gate.gate_id: gate for gate in baseline_draft.gates}
    current_gates = {gate.gate_id: gate for gate in current_draft.gates}
    gates: list[dict[str, object]] = []
    for gate_id in GATE_IDS:
        previous = baseline_gates.get(gate_id)
        latest = current_gates.get(gate_id)
        previous_result = previous.result if previous else None
        latest_result = latest.result if latest else None
        gates.append(
            {
                "gate_id": gate_id,
                "baseline_result": previous_result,
                "current_result": latest_result,
                "changed": previous_result != latest_result,
            }
        )

    baseline_moats = {moat.dimension_id: moat for moat in baseline_draft.moats}
    current_moats = {moat.dimension_id: moat for moat in current_draft.moats}
    moats: list[dict[str, object]] = []
    for dimension_id in MOAT_IDS:
        previous = baseline_moats.get(dimension_id)
        latest = current_moats.get(dimension_id)
        previous_score = previous.score if previous else None
        latest_score = latest.score if latest else None
        moats.append(
            {
                "dimension_id": dimension_id,
                "baseline_score": previous_score,
                "current_score": latest_score,
                "diff": _score_diff(previous_score, latest_score) if comparable else None,
                "changed": previous_score != latest_score,
            }
        )

    baseline_total = derive_score_total(baseline_draft)
    current_total = derive_score_total(current_draft)

    baseline_source_ids = {
        row["id"] for row in storage.get_run_sources(connection, baseline["id"])  # type: ignore[arg-type]
    }
    current_rows = storage.get_run_sources(connection, run["id"])  # type: ignore[arg-type]
    current_source_ids = {row["id"] for row in current_rows}
    new_source_ids = sorted(current_source_ids - baseline_source_ids)
    reused_source_ids = sorted(current_source_ids & baseline_source_ids)
    new_source_set = set(new_source_ids)
    new_sources: list[dict[str, object]] = []
    for row in current_rows:
        if row["id"] not in new_source_set:
            continue
        source = Source.model_validate_json(row["source_json"])
        new_sources.append(
            {
                "source_id": row["id"],
                "title": source.title,
                "kind": source.kind,
                "retrieved_at": row["retrieved_at"],
                "published_at": source.published_at.isoformat() if source.published_at else None,
            }
        )

    referenced = _draft_referenced_source_ids(current_draft)
    explanations = [
        {
            "target_id": explanation.target_id,
            "kind": explanation.statement.kind,
            "text": explanation.statement.text,
        }
        for explanation in current_draft.review_explanations
    ]
    return {
        "run_id": run["id"],
        "baseline_run_id": baseline["id"],
        "ticker": run["ticker"],
        "comparable": comparable,
        "baseline": {
            "as_of": baseline["as_of"],
            "completed_at": baseline["completed_at"],
            "schema_version": baseline_draft.schema_version,
        },
        "current": {
            "as_of": current_draft.as_of.isoformat(),
            "status": current_status or run["status"],
            "schema_version": current_draft.schema_version,
        },
        "score_total": {
            "baseline": baseline_total,
            "current": current_total,
            "diff": _score_diff(baseline_total, current_total) if comparable else None,
        },
        "answers": answers,
        "gates": gates,
        "moats": moats,
        "evidence": {
            "new_source_ids": new_source_ids,
            "reused_source_ids": reused_source_ids,
            "new_sources": new_sources,
            "referenced_new_source_ids": sorted(referenced & new_source_set),
            "referenced_reused_source_ids": sorted(referenced & set(reused_source_ids)),
        },
        "changes": {
            "answers_changed": sum(1 for item in answers if item["changed"]),
            "gates_changed": sum(1 for item in gates if item["changed"]),
            "moats_changed": sum(1 for item in moats if item["changed"]),
            "score_total_changed": (baseline_total != current_total),
        },
        "review_explanations": explanations,
    }


def compare_runs(root: Path, run_id: str) -> dict[str, object]:
    """与 baseline 比较结构化差异；草稿可预览，正式 review.md 在 finish 固化。"""
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        baseline_id = run["baseline_run_id"]
        if not baseline_id:
            raise ResearchError("no_baseline", "该 run 没有关联 baseline，无法 compare", exit_code=2)
        if not run["draft_json"]:
            raise ResearchError("empty_draft", "该 run 还没有保存草稿", exit_code=2)
        baseline = _require_run(connection, str(baseline_id))
        if baseline["status"] != "completed":
            raise ResearchError("baseline_not_completed", "基准 run 尚未完成", exit_code=2)
        return _build_comparison(connection, run, baseline)


def _backup_destination(root: Path, output: str) -> Path:
    backups_root = (root / "data" / "backups").resolve()
    requested = Path(output)
    destination = (root / requested).resolve() if not requested.is_absolute() else requested.resolve()
    if destination == backups_root or not destination.is_relative_to(backups_root):
        raise ResearchError(
            "invalid_backup_output", "--output 必须是 root/data/backups/ 下的新目录", exit_code=2
        )
    return destination


RESTORE_INSTRUCTIONS = """# 备份恢复说明

本目录由 `wonder-research backup` 生成，包含一致性的 SQLite 快照、已归档原文与完成报告。

恢复步骤（目标为任意项目根 `<root>`）：

1. 新建 `<root>` 并放入本项目的 `pyproject.toml`。
2. 复制 `wonder.db` 到 `<root>/data/wonder.db`。
3. 按 `manifest.json` 中 `files[].path`（项目相对路径），把 `files/<path>` 复制到 `<root>/<path>`。
4. 校验：对 `manifest.json` 的每个 `files[].sha256` 与实际文件比对；数据库 `database.sha256` 亦然。
5. 运行 `uv run wonder-research --root <root> history <TICKER>` 与 `status <RUN_ID>` 确认历史可读；
   `finish <RUN_ID>` 对已完成 run 只核对产物哈希，不应改写。

注意：备份是只读快照；恢复后不要在旧根上继续写入同一 run。
"""


def backup(
    root: Path,
    output: str,
    *,
    lock_timeout: float = storage.LOCK_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """在写锁内用 SQLite backup API + 文件拷贝生成备份；不覆盖已有备份。"""
    storage.initialize(root)
    destination = _backup_destination(root, output)
    with storage.project_lock(root, timeout=lock_timeout):
        if destination.exists():
            raise ResearchError("backup_exists", f"备份目录已存在：{output}", exit_code=2)
        files_root = destination / "files"
        files_root.mkdir(parents=True)
        database_path = destination / "wonder.db"
        storage.backup_database(root, database_path)
        entries: list[dict[str, object]] = []
        missing: list[str] = []
        with storage.open_db(root) as connection:
            sources = storage.list_all_sources(connection)
            completed_runs = storage.list_completed_runs(connection)
        for row in sources:
            for key in ("raw_path", "text_path"):
                relative = row.get(key)
                if not relative:
                    continue
                source_path = root / str(relative)
                if not source_path.is_file():
                    if str(relative).startswith("data/stocks/"):
                        missing.append(str(relative))
                    continue
                target = files_root / str(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target)
                entries.append(
                    {
                        "path": str(relative),
                        "kind": "source",
                        "sha256": storage.sha256_file(target),
                    }
                )
        for run in completed_runs:
            directory = storage.run_dir(
                root,
                run["ticker"],
                storage.parse_iso(run["created_at"]),
                run["id"],
            )
            if not directory.is_dir():
                missing.append(storage.relative_path(root, directory))
                continue
            for artifact in sorted(directory.iterdir()):
                if not artifact.is_file():
                    continue
                relative = storage.relative_path(root, artifact)
                target = files_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(artifact, target)
                entries.append(
                    {
                        "path": relative,
                        "kind": "run_artifact",
                        "sha256": storage.sha256_file(target),
                    }
                )
        manifest = {
            "created_at": storage.to_iso(storage.now_utc()),
            "schema_version": storage.SCHEMA_VERSION,
            "database": {
                "path": "wonder.db",
                "sha256": storage.sha256_file(database_path),
            },
            "file_count": len(entries),
            "files": entries,
            "missing": missing,
        }
        manifest_path = destination / "manifest.json"
        storage.write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        storage.write_atomic(destination / "RESTORE.md", RESTORE_INSTRUCTIONS)
    return {
        "output": storage.relative_path(root, destination),
        "database": "wonder.db",
        "file_count": len(entries),
        "missing": missing,
        "manifest": storage.relative_path(root, manifest_path),
        "manifest_sha256": storage.sha256_file(manifest_path),
    }
