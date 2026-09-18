"""阶段 1 的虚构教学材料；不代表任何真实公司或证券。"""

from __future__ import annotations

from copy import deepcopy

PRIMARY_SOURCE_ID = "11111111-1111-4111-8111-111111111111"
COUNTER_SOURCE_ID = "22222222-2222-4222-8222-222222222222"


def evidence(source_id: str = PRIMARY_SOURCE_ID, relation: str = "supports") -> dict[str, str]:
    return {
        "source_id": source_id,
        "locator": "第 1 页，教学段落",
        "relation": relation,
    }


def fact(statement_id: str, text: str, source_id: str = PRIMARY_SOURCE_ID) -> dict[str, object]:
    return {
        "id": statement_id,
        "kind": "fact",
        "text": text,
        "evidence": [evidence(source_id)],
        "numbers": [],
    }


def synthetic_packet_dict() -> dict[str, object]:
    answers = [
        {
            "question_id": f"Q{number:02d}",
            "status": "answered",
            "statements": [fact(f"answer-{number:02d}", f"SYNTHETIC 的 Q{number:02d} 教学事实。")],
            "gap": None,
        }
        for number in range(1, 25)
    ]
    answers[8]["statements"][0]["numbers"] = [
        {
            "value": "18.5",
            "unit": "%",
            "currency": None,
            "period": "2025 财年",
            "basis": "教学用 ROE",
            "evidence": [evidence()],
        }
    ]
    gates = [
        {
            "gate_id": gate_id,
            "result": "pass",
            "rationale": fact(f"gate-{gate_id}", f"{gate_id} 的教学门闸依据。"),
            "gap": None,
        }
        for gate_id in ("G1", "G2", "G3", "G5", "G6", "G7")
    ]
    moats = [
        {
            "dimension_id": dimension_id,
            "score": score,
            "confidence": "medium",
            "rationale": fact(f"moat-{dimension_id}", f"{dimension_id} 的教学评分依据。"),
            "gap": None,
        }
        for dimension_id, score in zip(
            ("M1", "M2", "M3", "M4", "M5", "M6"), (8, 6, None, 4, 7, 0), strict=True
        )
    ]
    return {
        "draft": {
            "schema_version": 1,
            "ticker": "SYNTHETIC",
            "as_of": "2026-09-10T12:00:00+00:00",
            "answers": answers,
            "gates": gates,
            "moats": moats,
            "challenge": {
                "counterargument": fact(
                    "challenge", "教学中的最强相反观点。", COUNTER_SOURCE_ID
                ),
                "channels_checked": ["教学资料库", "虚构公告索引"],
                "search_scope": "仅检查两份虚构材料。",
                "refutation_found": True,
                "overturning_evidence": "可重复核验的竞争劣势披露。",
                "next_check": "下一次手动复评。",
            },
            "conflicts": [],
            "conclusion": fact("conclusion", "这是不含估值的教学结论。"),
            "review_explanations": [],
        },
        "sources": [
            {
                "id": PRIMARY_SOURCE_ID,
                "ticker": "SYNTHETIC",
                "kind": "primary",
                "title": "SYNTHETIC 2025 年教学披露",
                "url": "https://example.invalid/synthetic-primary",
                "endpoint": None,
                "origin_file_label": None,
                "published_at": "2026-09-01T12:00:00+00:00",
                "publication_precision": "datetime",
                "publication_timezone": "UTC",
                "retrieved_at": "2026-09-02T12:00:00+00:00",
                "sha256": "a" * 64,
                "raw_path": "data/teaching/synthetic-primary.txt",
                "text_path": "data/teaching/synthetic-primary.txt",
                "extraction_status": "available",
                "metadata": {"teaching": True},
            },
            {
                "id": COUNTER_SOURCE_ID,
                "ticker": "SYNTHETIC",
                "kind": "secondary",
                "title": "SYNTHETIC 教学反方材料",
                "url": "https://example.invalid/synthetic-counterargument",
                "endpoint": None,
                "origin_file_label": None,
                "published_at": None,
                "publication_precision": "unknown",
                "publication_timezone": None,
                "retrieved_at": "2026-09-02T12:05:00+00:00",
                "sha256": "b" * 64,
                "raw_path": "data/teaching/synthetic-counterargument.txt",
                "text_path": "data/teaching/synthetic-counterargument.txt",
                "extraction_status": "available",
                "metadata": {"teaching": True},
            },
        ],
    }


def copy_synthetic_packet() -> dict[str, object]:
    return deepcopy(synthetic_packet_dict())
