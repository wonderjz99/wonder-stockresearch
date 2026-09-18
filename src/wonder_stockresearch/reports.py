"""阶段 1 的固定 Markdown 报告渲染，不写入文件。"""

from __future__ import annotations

from .models import EvidenceRef, ResearchPacket, Statement, source_id_text
from .research import derive_quality_status, derive_score_total


def _reference_text(reference: EvidenceRef) -> str:
    return f"`{source_id_text(reference.source_id)}`，{reference.relation}，{reference.locator}"


def _statement_lines(statement: Statement) -> list[str]:
    lines = [f"- {statement.kind}：{statement.text}"]
    for reference in statement.evidence:
        lines.append(f"  - 引用：{_reference_text(reference)}")
    for number in statement.numbers:
        currency = f" {number.currency}" if number.currency else ""
        lines.append(f"  - 数值：{number.value} {number.unit}{currency}；{number.period}；{number.basis}")
        for reference in number.evidence:
            lines.append(f"    - 数值引用：{_reference_text(reference)}")
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


def render_company(packet: ResearchPacket, *, archived: bool = False, run_id: str | None = None) -> str:
    """把已解析包变为固定结构 Markdown；调用方负责先校验。

    archived=False 用于阶段 1 的离线样例；archived=True 用于阶段 2 已写入数据库的正式记录。
    """
    draft = packet.draft
    total = derive_score_total(draft)
    total_text = str(total) if total is not None else "未评估（至少一项护城河评分为 null）"
    if archived:
        status_line = (
            f"> 状态：正式归档（run {run_id}）。完成仅表示通过结构与来源检查，不代表投资判断已证实。"
        )
    else:
        status_line = "> 状态：离线样例/未正式归档。本报告只验证结构与引用关系，不代表投资判断已证实。"
    lines = [
        "# 公司研究 — Standard 公司部分",
        "",
        status_line,
        "",
        f"- 股票：{draft.ticker}",
        f"- 研究截止时点：{draft.as_of.isoformat()}",
        f"- 质量状态：{derive_quality_status(draft)}",
        "- 护城河总分范围：0–60",
        f"- 总分：{total_text}",
        "",
        "## 公司问题 Q01–Q24",
        "",
    ]
    for answer in draft.answers:
        lines.extend([f"## {answer.question_id}", "", f"- 状态：{answer.status}"])
        for statement in answer.statements:
            lines.extend(_statement_lines(statement))
        lines.extend(_gap_lines(answer.gap))
        lines.append("")

    lines.extend(["## 公司门闸", ""])
    for gate in draft.gates:
        lines.extend([f"## {gate.gate_id}", "", f"- 结果：{gate.result}"])
        lines.extend(_statement_lines(gate.rationale))
        lines.extend(_gap_lines(gate.gap))
        lines.append("")

    lines.extend(["## 护城河评分", ""])
    for moat in draft.moats:
        score = str(moat.score) if moat.score is not None else "未评估（null）"
        lines.extend([f"## {moat.dimension_id}", "", f"- 分数：{score}", f"- 信心：{moat.confidence}"])
        lines.extend(_statement_lines(moat.rationale))
        lines.extend(_gap_lines(moat.gap))
        lines.append("")

    lines.extend(["## 反证与冲突", ""])
    lines.extend(_statement_lines(draft.challenge.counterargument))
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
            lines.append(f"- 冲突：{conflict.description}（关键：{conflict.critical}；已解决：{conflict.resolved}）")
            if conflict.resolution:
                lines.append(f"  - 解决方式：{conflict.resolution}")
    else:
        lines.append("- 未记录来源冲突。")
    lines.extend(["", "## 公司结论", ""])
    lines.extend(_statement_lines(draft.conclusion))
    lines.extend(["", "## 估值", "", "估值部分尚未实现；Q25–Q28 与 G4 不在本报告内。", "", "## 来源清单", ""])
    for source in packet.sources:
        published_at = source.published_at.isoformat() if source.published_at else "unknown"
        origin = source.url or source.endpoint or source.origin_file_label
        lines.append(
            f"- `{source_id_text(source.id)}`：{source.title}（{source.kind}；发布：{published_at}；{origin}）"
        )
    return "\n".join(lines) + "\n"
