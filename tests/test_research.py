from __future__ import annotations

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch.fixed.research import (
    derive_score_summary,
    derive_score_total,
    validate_packet,
)
from wonder_stockresearch.models import ResearchPacket


def test_complete_synthetic_packet_validates_and_unknown_score_stays_unknown() -> None:
    """Removing null propagation or a required coverage check must fail this test."""
    packet = ResearchPacket.model_validate(copy_synthetic_packet())

    assert validate_packet(packet) == []
    assert derive_score_total(packet.draft) is None
    summary = derive_score_summary(packet.draft)
    assert summary == {
        "score_total": None,
        "provisional_score_total": 28,
        "provisional_prior_per_missing_dimension": 3,
        "scored_dimension_count": 5,
        "missing_dimensions": ["M3"],
        "uncertainty_range": {"min": 25, "max": 35},
    }


def test_provisional_score_does_not_mutate_unknown_dimension() -> None:
    packet = ResearchPacket.model_validate(copy_synthetic_packet())

    derive_score_summary(packet.draft)

    assert packet.draft.moats[2].score is None


def test_validator_rejects_duplicate_question_and_unknown_evidence_source() -> None:
    """Accepting duplicate coverage or dangling evidence would make this test fail."""
    packet_data = copy_synthetic_packet()
    packet_data["draft"]["answers"][1]["question_id"] = "Q01"
    packet_data["draft"]["challenge"]["counterargument"]["evidence"][0]["source_id"] = (
        "33333333-3333-4333-8333-333333333333"
    )
    packet = ResearchPacket.model_validate(packet_data)

    issues = validate_packet(packet)

    assert {(issue.code, issue.location) for issue in issues} >= {
        ("duplicate_id", "draft.answers"),
        ("missing_id", "draft.answers"),
        ("unknown_source", "draft.challenge.counterargument.evidence[0]"),
    }


def test_model_rejects_missing_numeric_unit() -> None:
    """Allowing an empty numeric unit would make this test fail."""
    packet_data = copy_synthetic_packet()
    packet_data["draft"]["answers"][8]["statements"][0]["numbers"][0]["unit"] = ""

    try:
        ResearchPacket.model_validate(packet_data)
    except ValueError as error:
        assert "unit" in str(error)
    else:
        raise AssertionError("空 numeric unit 必须被拒绝")
