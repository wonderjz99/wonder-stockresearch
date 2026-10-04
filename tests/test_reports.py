from __future__ import annotations

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch.models import ResearchPacket
from wonder_stockresearch.reports import QUESTION_TEXT, render_company


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


def test_report_shows_all_full_question_text_and_chinese_status_labels() -> None:
    raw = copy_synthetic_packet()
    raw["draft"]["answers"][0].update(
        {
            "status": "partial",
            "gap": {
                "missing": "未找到",
                "where_to_find": "年报",
                "next_check": "下次复评",
                "impact": "影响判断",
            },
        }
    )
    raw["draft"]["gates"][0]["result"] = "insufficient"
    raw["draft"]["answers"][1]["statements"][0]["kind"] = "inference"
    packet = ResearchPacket.model_validate(raw)

    report = render_company(packet)

    for question_id, question in QUESTION_TEXT.items():
        assert f"## {question_id} — {question}" in report
    assert "状态：部分回答" in report
    assert "结果：证据不足" in report
    assert "推断：" in report
    assert "状态：partial" not in report


def test_short_citations_deduplicate_exact_matches_but_keep_different_evidence() -> None:
    raw = copy_synthetic_packet()
    statement = raw["draft"]["answers"][0]["statements"][0]
    original = statement["evidence"][0]
    statement["evidence"] = [
        original,
        dict(original),
        {**original, "locator": "第 2 页，另一段"},
        {**original, "relation": "refutes"},
    ]
    statement["numbers"] = [
        {
            "value": "1050187000000.1200",
            "unit": "CNY",
            "currency": "CNY",
            "period": "2025 财年",
            "basis": "教学口径",
            "evidence": [
                dict(original),
                dict(original),
                {**original, "locator": "第 3 页，数值表", "relation": "context"},
            ],
        }
    ]
    packet = ResearchPacket.model_validate(raw)

    report = render_company(packet)
    section = report.split("## Q01 —", maxsplit=1)[1].split("## Q02 —", maxsplit=1)[0]

    assert section.count("[S01] 支持，第 1 页，教学段落") == 1
    assert section.count("[S01] 支持，第 2 页，另一段") == 1
    assert section.count("[S01] 反驳，第 1 页，教学段落") == 1
    assert section.count("[S01] 背景，第 3 页，数值表") == 1
    assert "1,050,187,000,000.1200 CNY" in section
    assert "CNY CNY" not in section
    assert f"[S01] `{raw['sources'][0]['id']}`" in report
    assert f"[S02] `{raw['sources'][1]['id']}`" in report
