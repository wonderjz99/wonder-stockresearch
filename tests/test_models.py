from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from wonder_stockresearch.models import ResearchDraft


def minimal_draft() -> dict[str, object]:
    return {
        "schema_version": 1,
        "ticker": "SYNTHETIC",
        "as_of": "2026-09-10T12:00:00+00:00",
        "answers": [],
        "gates": [],
        "moats": [],
        "challenge": {
            "counterargument": {
                "id": "challenge",
                "kind": "assumption",
                "text": "教学用反方观点。",
                "evidence": [],
                "numbers": [],
            },
            "channels_checked": ["教学资料库"],
            "search_scope": "离线样例",
            "refutation_found": False,
            "overturning_evidence": "出现可核验的反方披露。",
            "next_check": "下一次手动复评。",
        },
        "conflicts": [],
        "conclusion": {
            "id": "conclusion",
            "kind": "assumption",
            "text": "教学结论。",
            "evidence": [],
            "numbers": [],
        },
        "review_explanations": [],
    }


def test_draft_rejects_valuation_field_and_out_of_range_moat_score() -> None:
    """A model change that accepts valuation input or a score above 10 must fail here."""
    draft = minimal_draft()
    draft["valuation"] = {"target_price": "100"}
    draft["moats"] = [
        {
            "dimension_id": "M1",
            "score": 11,
            "confidence": "medium",
            "rationale": {
                "id": "m1",
                "kind": "assumption",
                "text": "教学评分理由。",
                "evidence": [],
                "numbers": [],
            },
            "gap": None,
        }
    ]

    with pytest.raises(ValidationError) as exc_info:
        ResearchDraft.model_validate(deepcopy(draft))

    error_locations = {tuple(error["loc"]) for error in exc_info.value.errors()}
    assert ("valuation",) in error_locations
    assert ("moats", 0, "score") in error_locations
