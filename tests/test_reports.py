from __future__ import annotations

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch.models import ResearchPacket
from wonder_stockresearch.reports import render_company


def test_rendered_packet_marks_teaching_status_and_keeps_null_total_unknown() -> None:
    """Dropping the teaching/valuation labels or turning null into zero must fail here."""
    packet = ResearchPacket.model_validate(copy_synthetic_packet())

    report = render_company(packet)

    assert "# 公司研究 — Standard 公司部分" in report
    assert "离线样例/未正式归档" in report
    assert "总分：未评估（至少一项护城河评分为 null）" in report
    assert "估值部分尚未实现" in report
    assert "## Q01" in report
    assert "## Q24" in report
    assert "## G1" in report
    assert "## M6" in report
