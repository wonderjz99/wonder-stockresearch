"""固定 Markdown 报告与复评报告渲染；不改写已完成产物。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from wonder_stockresearch.fixed.questions import QUESTION_TEXT
from wonder_stockresearch.fixed.research import derive_quality_status, derive_score_summary
from wonder_stockresearch.models import (
    DraftReview,
    EvidenceRef,
    ResearchPacket,
    Statement,
    source_id_text,
)

_ANSWER_STATUS = {"answered": "已回答", "partial": "部分回答", "missing": "缺少资料"}
_STATEMENT_KIND = {"fact": "事实", "inference": "推断", "assumption": "假设"}
_EVIDENCE_RELATION = {"supports": "支持", "refutes": "反驳", "context": "背景"}
_GATE_RESULT = {"pass": "通过", "fail": "未通过", "insufficient": "证据不足"}
_CONFIDENCE = {"low": "低", "medium": "中", "high": "高"}
_SOURCE_KIND = {
    "primary": "一手披露",
    "provider": "数据接口",
    "secondary": "二手资料",
    "legacy": "历史资料",
}


def _source_aliases(packet: ResearchPacket) -> dict[str, str]:
    source_ids = sorted(source_id_text(source.id) for source in packet.sources)
    return {source_id: f"S{index:02d}" for index, source_id in enumerate(source_ids, start=1)}


def _unique_references(references: list[EvidenceRef]) -> list[EvidenceRef]:
    unique: list[EvidenceRef] = []
    seen: set[tuple[str, str, str]] = set()
    for reference in references:
        key = (source_id_text(reference.source_id), reference.locator, reference.relation)
        if key not in seen:
            unique.append(reference)
            seen.add(key)
    return unique


def _reference_text(reference: EvidenceRef, aliases: dict[str, str]) -> str:
    source_id = source_id_text(reference.source_id)
    alias = aliases.get(source_id, source_id)
    relation = _EVIDENCE_RELATION.get(reference.relation, reference.relation)
    return f"[{alias}] {relation}，{reference.locator}"


def _references_line(
    label: str, references: list[EvidenceRef], aliases: dict[str, str]
) -> list[str]:
    if not references:
        return []
    rendered = "；".join(
        _reference_text(reference, aliases) for reference in _unique_references(references)
    )
    return [f"  - {label}：{rendered}"]


def _format_number(value: Decimal) -> str:
    """千位分组但保留 Decimal 的所有小数位，不做舍入。"""
    return format(value, ",f")


def _number_units(unit: str, currency: str | None) -> str:
    values = [unit]
    if currency and currency.casefold() != unit.casefold():
        values.append(currency)
    return " ".join(values)


def _statement_lines(statement: Statement, aliases: dict[str, str]) -> list[str]:
    kind = _STATEMENT_KIND.get(statement.kind, statement.kind)
    lines = [f"- {kind}：{statement.text}"]
    lines.extend(_references_line("证据", statement.evidence, aliases))
    statement_keys = {
        (source_id_text(reference.source_id), reference.locator, reference.relation)
        for reference in statement.evidence
    }
    for number in statement.numbers:
        units = _number_units(number.unit, number.currency)
        lines.append(
            f"  - 数值：{_format_number(number.value)} {units}；{number.period}；{number.basis}"
        )
        distinct_references = [
            reference
            for reference in _unique_references(number.evidence)
            if (source_id_text(reference.source_id), reference.locator, reference.relation)
            not in statement_keys
        ]
        lines.extend(_references_line("数值证据", distinct_references, aliases))
    return lines


def _gap_lines(gap: object) -> list[str]:
    if gap is None:
        return []
    return [
        f"- 缺口：{gap.missing}",
        f"  - 下一来源/方法：{gap.where_to_find}",
        f"  - 下次核验：{gap.next_check}",
        f"  - 影响：{gap.impact}",
    ]


def render_company(
    packet: ResearchPacket,
    *,
    archived: bool = False,
    preview: bool = False,
    run_id: str | None = None,
) -> str:
    """把已解析包变为固定结构 Markdown；调用方负责先校验。

    archived=False 用于离线样例；archived=True 用于已写入数据库的正式记录。
    """
    draft = packet.draft
    aliases = _source_aliases(packet)
    score_summary = derive_score_summary(draft)
    total = score_summary["score_total"]
    total_text = str(total) if total is not None else "未评估（至少一项护城河评分为 null）"
    provisional = score_summary["provisional_score_total"]
    coverage = f"{score_summary['scored_dimension_count']}/6"
    uncertainty = score_summary["uncertainty_range"]
    missing = score_summary["missing_dimensions"]
    provisional_note = (
        f"{provisional}（暂定；证据覆盖 {coverage}；"
        f"未评估维度 {', '.join(missing)} 各按 {score_summary['provisional_prior_per_missing_dimension']} 分计入；"
        f"不确定区间 {uncertainty['min']}–{uncertainty['max']}）"
        if missing
        else f"{provisional}（六项均有证据评分）"
    )
    quality = "完整" if derive_quality_status(draft) == "complete" else "证据不完整"
    if archived:
        status_line = f"> 状态：正式归档（run {run_id}）。完成仅表示通过结构与来源检查，不代表投资判断已证实。"
    elif preview:
        status_line = (
            f"> 状态：草稿预览（run {run_id}）。本文件只供验收查看，未执行 finish，"
            "不构成正式归档或投资判断。"
        )
    else:
        status_line = (
            "> 状态：离线样例/未正式归档。本报告只验证结构与引用关系，不代表投资判断已证实。"
        )
    lines = [
        "# 公司研究 — Standard 公司部分",
        "",
        status_line,
        "",
        f"- 股票：{draft.ticker}",
        f"- 研究截止时点：{draft.as_of.isoformat()}",
        f"- 质量状态：{quality}",
        "- 护城河总分范围：0–60",
        f"- 严格证据总分：{total_text}",
        f"- 可用暂定总分：{provisional_note}",
        "",
        "## 公司问题 Q01–Q24",
        "",
    ]
    for answer in draft.answers:
        question = QUESTION_TEXT.get(answer.question_id, "")
        heading = (
            f"## {answer.question_id} — {question}" if question else f"## {answer.question_id}"
        )
        lines.extend([heading, "", f"- 状态：{_ANSWER_STATUS.get(answer.status, answer.status)}"])
        for statement in answer.statements:
            lines.extend(_statement_lines(statement, aliases))
        lines.extend(_gap_lines(answer.gap))
        lines.append("")

    lines.extend(["## 公司门闸", ""])
    for gate in draft.gates:
        lines.extend(
            ["## " + gate.gate_id, "", f"- 结果：{_GATE_RESULT.get(gate.result, gate.result)}"]
        )
        lines.extend(_statement_lines(gate.rationale, aliases))
        lines.extend(_gap_lines(gate.gap))
        lines.append("")

    lines.extend(["## 护城河评分", ""])
    for moat in draft.moats:
        score = str(moat.score) if moat.score is not None else "未评估（null）"
        confidence = _CONFIDENCE.get(moat.confidence, moat.confidence)
        lines.extend([f"## {moat.dimension_id}", "", f"- 分数：{score}", f"- 信心：{confidence}"])
        lines.extend(_statement_lines(moat.rationale, aliases))
        lines.extend(_gap_lines(moat.gap))
        lines.append("")

    lines.extend(["## 反证与冲突", ""])
    lines.extend(_statement_lines(draft.challenge.counterargument, aliases))
    lines.extend(
        [
            f"- 已检索渠道：{'、'.join(draft.challenge.channels_checked)}",
            f"- 检索范围：{draft.challenge.search_scope}",
            f"- 是否找到反方来源：{'是' if draft.challenge.refutation_found else '否'}",
            f"- 可能推翻结论的新证据：{draft.challenge.overturning_evidence}",
            f"- 下次核验：{draft.challenge.next_check}",
            "",
        ]
    )
    if draft.conflicts:
        for conflict in draft.conflicts:
            conflict_sources = "、".join(
                f"[{aliases.get(source_id_text(source_id), source_id_text(source_id))}]"
                for source_id in conflict.source_ids
            )
            lines.append(
                f"- 冲突：{conflict.description}（关键：{'是' if conflict.critical else '否'}；"
                f"已解决：{'是' if conflict.resolved else '否'}；来源：{conflict_sources}）"
            )
            if conflict.resolution:
                lines.append(f"  - 解决方式：{conflict.resolution}")
    else:
        lines.append("- 未记录来源冲突。")
    lines.extend(["", "## 公司结论", ""])
    lines.extend(_statement_lines(draft.conclusion, aliases))
    lines.extend(
        ["", "## 估值", "", "估值部分尚未实现；Q25–Q28 与 G4 不在本报告内。", "", "## 来源索引", ""]
    )
    source_by_id = {source_id_text(source.id): source for source in packet.sources}
    for source_id in sorted(source_by_id):
        source = source_by_id[source_id]
        published_at = source.published_at.isoformat() if source.published_at else "未知"
        origin = source.url or source.endpoint or source.origin_file_label or "未提供"
        retrieved_at = source.retrieved_at.isoformat()
        extraction = (
            "可读取" if source.extraction_status == "available" else source.extraction_status
        )
        alias = aliases[source_id]
        lines.append(
            f"- [{alias}] `{source_id}`：{source.title}（{_SOURCE_KIND.get(source.kind, source.kind)}；"
            f"发布：{published_at}；获取：{retrieved_at}；提取：{extraction}；来源：{origin}）"
        )
    return "\n".join(lines) + "\n"


def render_draft_review(review: DraftReview, *, run_id: str) -> str:
    """把已保存的结构化语义审查渲染为仅供验收查看的 Markdown。"""
    lines = [
        "# 固定研究草稿语义审查",
        "",
        f"> 草稿 run：`{run_id}`。本文件是审查预览，不会改变草稿或正式归档状态。",
        "",
        f"- 草稿哈希：`{review.draft_hash}`",
        f"- 审查状态：{'通过' if review.status == 'passed' else '未通过'}",
        f"- 覆盖：{len(review.checked_questions)} 个问题、{len(review.checked_gates)} 个门闸、{len(review.checked_moats)} 个护城河维度",
        f"- 问题数：{len(review.issues)}",
        "",
        "## 问题清单",
        "",
    ]
    if not review.issues:
        lines.append("- 未记录语义审查问题。")
    for index, issue in enumerate(review.issues, start=1):
        severity = "错误" if issue.severity == "error" else "警告"
        lines.extend(
            [
                f"## {index}. {severity} — {issue.target}",
                "",
                f"- 类别：{issue.category}",
                f"- 说明：{issue.message}",
                f"- 修订建议：{issue.revision_instruction}",
            ]
        )
        lines.extend(_references_line("相关引用", issue.evidence, {}))
        lines.append("")
    return "\n".join(lines) + "\n"


def _value_or_unknown(value: object) -> str:
    return "未评估（null）" if value is None else str(value)


def _diff_text(diff: object) -> str:
    if diff is None:
        return "不适用（缺少分值或不可比）"
    return str(diff)


def render_review(comparison: dict[str, Any]) -> str:
    """把 compare 的结构化差异渲染为固定 Markdown；不改写旧报告。"""
    baseline = comparison["baseline"]
    current = comparison["current"]
    total = comparison["score_total"]
    evidence = comparison["evidence"]
    lines = [
        "# 公司研究复评 — Standard 公司部分",
        "",
        "> 复评对比由程序计算；完成仅表示通过结构检查，不代表判断已证实。旧报告与旧原文未被改写。",
        "",
        f"- 本次 run：{comparison['run_id']}",
        f"- 基准 run：{comparison['baseline_run_id']}",
        f"- 股票：{comparison['ticker']}",
        f"- 基准 as_of：{baseline['as_of']}",
        f"- 本次 as_of：{current['as_of']}",
        f"- 可直接比较：{'是' if comparison['comparable'] else '否（schema 或口径不同，不计算分差）'}",
        "",
        "## 护城河总分",
        "",
        f"- 基准：{_value_or_unknown(total['baseline'])}",
        f"- 本次：{_value_or_unknown(total['current'])}",
        f"- 差值：{_diff_text(total['diff'])}",
        "",
        "## 问题状态变化",
        "",
    ]
    for answer in comparison["answers"]:
        mark = "（变化）" if answer["changed"] else ""
        lines.append(
            f"- {answer['question_id']}：{answer['baseline_status']} → {answer['current_status']}{mark}"
        )
    lines.extend(["", "## 公司门闸变化", ""])
    for gate in comparison["gates"]:
        mark = "（变化）" if gate["changed"] else ""
        lines.append(
            f"- {gate['gate_id']}：{gate['baseline_result']} → {gate['current_result']}{mark}"
        )
    lines.extend(["", "## 护城河评分变化", ""])
    for moat in comparison["moats"]:
        lines.append(
            f"- {moat['dimension_id']}：{_value_or_unknown(moat['baseline_score'])} → "
            f"{_value_or_unknown(moat['current_score'])}；差值 {_diff_text(moat['diff'])}"
        )
    lines.extend(
        [
            "",
            "## 证据变化",
            "",
            f"- 本次新增来源：{len(evidence['new_source_ids'])} 条",
            f"- 沿用基准来源：{len(evidence['reused_source_ids'])} 条",
            "- 旧来源保留其首次获取时间与发布时间，不得冒充本次新取得。",
            "",
        ]
    )
    if evidence["new_sources"]:
        lines.append("- 新增来源明细：")
        for source in evidence["new_sources"]:
            lines.append(
                f"  - `{source['source_id']}`：{source['title']}（{source['kind']}；"
                f"获取 {source['retrieved_at']}；发布 {source['published_at']}）"
            )
        lines.append("")
    lines.extend(["## 变化解释", ""])
    if comparison["review_explanations"]:
        for explanation in comparison["review_explanations"]:
            lines.append(
                f"- {explanation['target_id']}（{explanation['kind']}）：{explanation['text']}"
            )
    else:
        lines.append("- 本次草稿未提供 review_explanations；程序只列出结构差异。")
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 旧材料作为历史保留，不改写；缺分值时差值显示“不适用”。",
            "- 不使用价格变动解释公司分数变化。",
            "",
        ]
    )
    return "\n".join(lines)
