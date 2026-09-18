"""阶段 1 的纯业务校验与派生值计算。"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from . import storage
from .models import (
    GATE_IDS,
    MOAT_IDS,
    QUESTION_IDS,
    EvidenceRef,
    ResearchDraft,
    ResearchPacket,
    Source,
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


def _has_authoritative_support(statement: Statement, sources: dict[str, Source]) -> bool:
    return any(
        reference.relation == "supports"
        and sources.get(source_id_text(reference.source_id), None) is not None
        and sources[source_id_text(reference.source_id)].kind in {"primary", "provider"}
        for reference, _ in _statement_references(statement, "statement")
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
        for statement_index, statement in enumerate(answer.statements):
            statement_location = f"{answer_location}.statements[{statement_index}]"
            _append_unknown_source_issues(issues, statement, statement_location, sources)
            if answer.status == "answered" and not _has_authoritative_support(statement, sources):
                issues.append(
                    ValidationIssue(
                        "missing_authoritative_support",
                        statement_location,
                        "已回答的问题需要 primary 或 provider 的 supports 引用",
                    )
                )

    for gate_index, gate in enumerate(draft.gates):
        gate_location = f"draft.gates[{gate_index}].rationale"
        _append_unknown_source_issues(issues, gate.rationale, gate_location, sources)
        if gate.result in {"pass", "fail"} and not _has_authoritative_support(gate.rationale, sources):
            issues.append(
                ValidationIssue(
                    "missing_authoritative_support",
                    gate_location,
                    "pass 或 fail 门闸需要 primary 或 provider 的 supports 引用",
                )
            )

    for moat_index, moat in enumerate(draft.moats):
        moat_location = f"draft.moats[{moat_index}].rationale"
        _append_unknown_source_issues(issues, moat.rationale, moat_location, sources)
        if moat.score is not None and not _has_authoritative_support(moat.rationale, sources):
            issues.append(
                ValidationIssue(
                    "missing_authoritative_support",
                    moat_location,
                    "非 null 评分需要 primary 或 provider 的 supports 引用",
                )
            )

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
        issues = validate_packet(packet)
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
    return {"run_id": run_id, "status": "draft", "saved": True}, issues


def validate_run(root: Path, run_id: str) -> list[ValidationIssue]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = _require_run(connection, run_id)
        if not run["draft_json"]:
            return [ValidationIssue("empty_draft", "draft", "该 run 还没有保存草稿")]
        packet = _packet_from_run(connection, run)
        return list(validate_packet(packet))


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
    from .reports import render_company

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
        issues = validate_packet(packet)
        if issues:
            raise ResearchError(
                "validation_failed", "草稿未通过校验，未生成正式报告", exit_code=2, issues=issues
            )
        draft = packet.draft
        company_markdown = render_company(packet, archived=True, run_id=run_id)
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
        directory = storage.run_dir(
            root, run["ticker"], storage.parse_iso(run["created_at"]), run_id  # type: ignore[arg-type]
        )
        try:
            storage.write_atomic(directory / "company.md", company_markdown)
            storage.write_atomic(
                directory / "research.json",
                json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            )
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
        if run["error_json"]:
            data["error"] = json.loads(run["error_json"])
    return data


def get_history(root: Path, ticker: str) -> dict[str, object]:
    _checked_ticker(ticker)
    storage.initialize(root)
    with storage.open_db(root) as connection:
        return {"ticker": ticker, "runs": storage.list_runs(connection, ticker)}
