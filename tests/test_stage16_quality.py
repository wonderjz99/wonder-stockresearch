from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import fixed_quality, research, storage, writer
from wonder_stockresearch.models import (
    Answer,
    DraftReview,
    EvidenceRef,
    Gap,
    NumericFact,
    ResearchPacket,
    Source,
)


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'\nversion='0'\n", encoding="utf-8")
    return root


def packet_for(ticker: str = "SYNTHETIC") -> ResearchPacket:
    data = copy_synthetic_packet()
    data["draft"]["ticker"] = ticker
    for source in data["sources"]:
        source["ticker"] = ticker
        source["metadata"].pop("teaching", None)
    return ResearchPacket.model_validate(data)


def passing_review(packet: ResearchPacket) -> DraftReview:
    return DraftReview(
        draft_hash=fixed_quality.draft_hash(packet.draft),
        status="passed",
        checked_questions=list(fixed_quality.QUESTION_IDS),
        checked_gates=list(fixed_quality.GATE_IDS),
        checked_moats=list(fixed_quality.MOAT_IDS),
        issues=[],
    )


def save_human_review(root: Path, run_id: str, packet: ResearchPacket) -> None:
    research.save_draft_review(
        root, run_id, passing_review(packet), reviewer_type="human",
        provider=None, model=None, template_version=fixed_quality.REVIEW_TEMPLATE_VERSION,
    )


def test_schema_v6_new_standard_run_requires_matching_review_and_freezes_it(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    packet = packet_for()
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet)
    assert {issue.code for issue in research.validate_run(root, run_id)} == {"semantic_review_missing"}

    save_human_review(root, run_id, packet)
    assert research.validate_run(root, run_id) == []
    result = research.finish_run(root, run_id)
    assert "quality-review.json" in result["artifacts"]
    assert (root / str(result["run_dir"]) / "quality-review.json").is_file()


def test_resaving_draft_makes_old_review_stale(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    packet = packet_for()
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet)
    save_human_review(root, run_id, packet)
    changed = packet.model_copy(deep=True)
    changed.draft.answers[0].statements[0].text = "改变后的事实"
    research.save_draft(root, run_id, changed)
    assert "semantic_review_stale" in {issue.code for issue in research.validate_run(root, run_id)}


def test_draft_and_review_can_be_read_without_changing_the_run(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    packet = packet_for()
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet)

    draft_data = research.get_draft(root, run_id)
    assert draft_data["status"] == "draft"
    assert draft_data["draft"] == packet.draft.model_dump(mode="json")

    save_human_review(root, run_id, packet)
    review_data = research.get_draft_review(root, run_id)
    assert review_data["review"]["status"] == "passed"
    assert research.get_status(root, run_id)["status"] == "draft"


def test_draft_preview_writes_non_authoritative_markdown_without_changing_run(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    packet = packet_for()
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet)
    save_human_review(root, run_id, packet)

    result = research.preview_draft(root, run_id)

    draft_path = root / str(result["artifacts"]["draft.md"])
    review_path = root / str(result["artifacts"]["quality-review.md"])
    assert "草稿预览" in draft_path.read_text(encoding="utf-8")
    assert "固定研究草稿语义审查" in review_path.read_text(encoding="utf-8")
    assert research.get_status(root, run_id)["status"] == "draft"


def test_review_rejects_incomplete_coverage_and_illegal_reference(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    packet = packet_for()
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet)
    review = passing_review(packet).model_copy(deep=True)
    review.checked_questions.pop()
    with pytest.raises(research.ResearchError) as exc_info:
        research.save_draft_review(root, run_id, review, reviewer_type="human", provider=None, model=None, template_version="test")
    assert exc_info.value.code == "invalid_semantic_review"

    illegal = passing_review(packet).model_dump(mode="json")
    illegal["issues"] = [{
        "category": "evidence_support", "target": "Q01", "severity": "warning",
        "message": "需人工核对", "revision_instruction": "核对来源",
        "evidence": [{"source_id": "99999999-9999-4999-8999-999999999999", "locator": "page 1", "relation": "context"}],
    }]
    with pytest.raises(research.ResearchError) as illegal_error:
        research.save_draft_review(
            root, run_id, DraftReview.model_validate(illegal), reviewer_type="human",
            provider=None, model=None, template_version="test",
        )
    assert {issue.code for issue in illegal_error.value.issues} == {
        "semantic_review_illegal_reference"
    }


def test_trusted_calculations_use_decimal_period_currency_and_sources(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    text_path = root / "provider.json"
    text_path.write_text(json.dumps({"dataset": "financials", "rows": [
        {"display_name": "净利润", "period_text": "2023", "data": "100", "currency": "CNY"},
        {"display_name": "净利润", "period_text": "2024", "data": "110", "currency": "CNY"},
        {"display_name": "净资产收益率(ROE)", "period_text": "2023", "data": "16.2", "unit": "%"},
        {"display_name": "净资产收益率(ROE)", "period_text": "2024", "data": "15.8", "unit": "%"},
        {"display_name": "营业收入", "period_text": "2024", "data": "1000", "currency": "CNY"},
        {"display_name": "资本开支", "period_text": "2024", "data": "200", "currency": "CNY"},
    ]}, ensure_ascii=False), encoding="utf-8")
    source = Source.model_validate({
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "ticker": "SYNTHETIC",
        "kind": "provider", "title": "结构化财务", "endpoint": "test://financials",
        "published_at": None, "publication_precision": "unknown", "publication_timezone": None,
        "retrieved_at": datetime.now(UTC), "sha256": "a" * 64,
        "raw_path": "provider.json", "text_path": "provider.json", "extraction_status": "available",
        "metadata": {"dataset": "financials", "available_from": "2025-01-01T00:00:00+00:00"},
    })
    results = {item.metric: item for item in fixed_quality.calculate_trusted_metrics(root, [source])}
    assert results["net_profit_yoy_growth"].values[0]["value"] == Decimal("10.0")
    assert results["roe_series"].summary["covered_years"] == 2
    assert results["roe_series"].summary["years_above_15_percent"] == 2
    assert results["capex_to_revenue"].values[0]["value"] == Decimal("20.0")
    assert results["capex_to_revenue"].source_refs


def test_futu_statement_items_inherit_report_period_and_build_ten_year_roe(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    path = root / "futu-statements.json"
    reports = [
        {
            "period_text": f"{year}/FY", "date_time_str": f"{year}-12-31",
            "currency_code": "CNY", "accounting_standards": "国际会计准则",
            "item_list": [{"display_name": "净资产收益率(ROE)", "data": str(year - 2000)}],
        }
        for year in range(2025, 2015, -1)
    ]
    path.write_text(json.dumps({
        "provider": "futu", "dataset": "statements",
        "payload": {"statements": {"4": {"report_list": reports}}},
    }, ensure_ascii=False), encoding="utf-8")
    source = Source.model_validate({
        "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "ticker": "SYNTHETIC",
        "kind": "provider", "title": "Futu 财务", "endpoint": "futu.statements",
        "published_at": None, "publication_precision": "unknown", "publication_timezone": None,
        "retrieved_at": datetime.now(UTC), "sha256": "b" * 64,
        "raw_path": "futu-statements.json", "text_path": "futu-statements.json",
        "extraction_status": "available",
        "metadata": {"dataset": "statements", "available_from": "2025-12-31"},
    })

    metrics = {item.metric: item for item in fixed_quality.calculate_trusted_metrics(root, [source])}
    roe = metrics["roe_series"]

    assert [row["period"] for row in roe.values] == [f"{year}/FY" for year in range(2016, 2026)]
    assert roe.summary["covered_years"] == 10
    assert all(item.currency is None for item in roe.inputs)
    assert all(item.unit == "%" for item in roe.inputs)
    assert all("report_list" in item.locator for item in roe.inputs)

    pack = fixed_quality.build_evidence_pack(root, "Q09-Q12", [source], datetime(2026, 1, 1, tzinfo=UTC))
    roe_chunks = pack.question_chunks["Q09"]
    assert roe_chunks
    assert all(chunk.currency == "CNY" for chunk in roe_chunks)
    assert all(chunk.accounting_standard == "国际会计准则" for chunk in roe_chunks)


def test_page_fts_index_keeps_question_specific_evidence_reservations(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    path = root / "annual.txt"
    path.write_text(
        "Business review of consumer and home market.\f"
        "Capital expenditure was RMB100 and free cash flow was RMB20.\f"
        "Principal risks include network and data security risks.\f"
        "5G-Advanced technology and artificial intelligence products expanded.",
        encoding="utf-8",
    )
    source = Source.model_validate({
        "id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "ticker": "SYNTHETIC",
        "kind": "primary", "title": "Annual Report", "url": "https://example.test/annual.pdf",
        "published_at": "2025-03-01T00:00:00+00:00", "publication_precision": "datetime",
        "publication_timezone": "UTC", "retrieved_at": datetime.now(UTC), "sha256": "c" * 64,
        "raw_path": "annual.txt", "text_path": "annual.txt", "extraction_status": "available", "metadata": {},
    })

    business = fixed_quality.build_evidence_pack(root, "Q01-Q04", [source], datetime(2026, 1, 1, tzinfo=UTC))
    risks = fixed_quality.build_evidence_pack(root, "Q21-Q24", [source], datetime(2026, 1, 1, tzinfo=UTC))

    assert [chunk.locator for chunk in business.question_chunks["Q03"]] == ["page 2"]
    assert [chunk.locator for chunk in business.question_chunks["Q04"]] == ["page 4"]
    assert [chunk.locator for chunk in risks.question_chunks["Q21"]] == ["page 3"]
    with storage.open_db(root) as connection:
        rows = storage.source_page_rows(connection, [str(source.id)])
    assert [row["locator"] for row in rows] == ["page 1", "page 2", "page 3", "page 4"]


def test_cash_capex_proxy_is_explicitly_labeled_and_uses_absolute_cash_outflows(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    path = root / "cashflow.json"
    path.write_text(json.dumps({"rows": [
        {"display_name": "购买固定资产", "period_text": "2025/FY", "data": "-200", "currency": "CNY"},
        {"display_name": "购买无形资产", "period_text": "2025/FY", "data": "-30", "currency": "CNY"},
    ]}, ensure_ascii=False), encoding="utf-8")
    source = Source.model_validate({
        "id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "ticker": "SYNTHETIC",
        "kind": "provider", "title": "现金流", "endpoint": "test://cashflow",
        "published_at": None, "publication_precision": "unknown", "publication_timezone": None,
        "retrieved_at": datetime.now(UTC), "sha256": "d" * 64,
        "raw_path": "cashflow.json", "text_path": "cashflow.json", "extraction_status": "available",
        "metadata": {"dataset": "statements", "available_from": "2025-12-31"},
    })

    metrics = {item.metric: item for item in fixed_quality.calculate_trusted_metrics(root, [source])}

    assert metrics["cash_capex_proxy"].values == [{
        "period": "2025/FY", "value": Decimal(230), "unit": "reported currency", "currency": "CNY",
        "basis": "cash-flow proxy: purchases of fixed assets and intangible assets",
    }]


def test_capex_to_revenue_uses_an_unambiguous_annual_report_disclosure(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    annual_path = root / "annual.txt"
    annual_path.write_text(
        "Capital expenditure totaled RMB200 million during the year.\f"
        "Capital expenditure totaled RMB200 million during the year.", encoding="utf-8"
    )
    provider_path = root / "financials.json"
    provider_path.write_text(json.dumps({"rows": [
        {"display_name": "营业总收入", "period_text": "2025/FY", "data": "1000000000", "currency": "CNY"},
    ]}, ensure_ascii=False), encoding="utf-8")
    annual = Source.model_validate({
        "id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "ticker": "SYNTHETIC", "kind": "primary",
        "title": "Annual Report 2025", "url": "https://example.test/annual.pdf",
        "published_at": "2026-03-01T00:00:00+00:00", "publication_precision": "datetime",
        "publication_timezone": "UTC", "retrieved_at": datetime.now(UTC), "sha256": "e" * 64,
        "raw_path": "annual.txt", "text_path": "annual.txt", "extraction_status": "available",
        "metadata": {"role": "annual"},
    })
    provider = Source.model_validate({
        "id": "ffffffff-ffff-4fff-8fff-ffffffffffff", "ticker": "SYNTHETIC", "kind": "provider",
        "title": "收入", "endpoint": "test://financials", "published_at": None,
        "publication_precision": "unknown", "publication_timezone": None, "retrieved_at": datetime.now(UTC),
        "sha256": "f" * 64, "raw_path": "financials.json", "text_path": "financials.json",
        "extraction_status": "available", "metadata": {"dataset": "statements", "available_from": "2025-12-31"},
    })

    metrics = {item.metric: item for item in fixed_quality.calculate_trusted_metrics(root, [annual, provider])}

    assert metrics["capex_to_revenue"].values == [{"period": "2025/FY", "value": Decimal(20), "unit": "%"}]
    assert metrics["capex_to_revenue"].inputs[0].label == "reported capital expenditure"


def test_annual_capex_prefers_detailed_year_disclosure_over_rounded_summary(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    annual_path = root / "annual.txt"
    annual_path.write_text(
        "Capital expenditure was RMB92.5 billion.\f"
        "Capital expenditure for the year was RMB92,528 million.", encoding="utf-8"
    )
    source = Source.model_validate({
        "id": "abababab-abab-4aba-8aba-abababababab", "ticker": "SYNTHETIC", "kind": "primary",
        "title": "Annual Report 2025", "url": "https://example.test/annual.pdf",
        "published_at": "2026-03-01T00:00:00+00:00", "publication_precision": "datetime",
        "publication_timezone": "UTC", "retrieved_at": datetime.now(UTC), "sha256": "a" * 64,
        "raw_path": "annual.txt", "text_path": "annual.txt", "extraction_status": "available",
        "metadata": {"role": "annual"},
    })

    inputs = fixed_quality._annual_capex_inputs(root, [source])

    assert [(item.value, item.locator) for item in inputs] == [(Decimal(92_528_000_000), "page 2")]


def test_staged_writer_uses_six_groups_synthesis_review_and_reuses_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    packet = packet_for()
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet)
    draft = packet.draft.model_dump(mode="json")
    responses = [
        {"answers": draft["answers"][offset : offset + 4]}
        for offset in range(0, 24, 4)
    ]
    responses.append({key: draft[key] for key in ("gates", "moats", "challenge", "conflicts", "conclusion", "review_explanations")})
    responses.append({
        "draft_hash": "0" * 64, "status": "passed",
        "checked_questions": list(fixed_quality.QUESTION_IDS),
        "checked_gates": list(fixed_quality.GATE_IDS), "checked_moats": list(fixed_quality.MOAT_IDS),
        "issues": [],
    })
    queue = iter(responses)

    def request(**_: object) -> dict[str, object]:
        return {"choices": [{"message": {"content": json.dumps(next(queue), ensure_ascii=False)}, "finish_reason": "stop"}], "usage": {"total_tokens": 10}}

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(writer, "_request_deepseek", request)
    data, issues = writer.write_draft(root, run_id)
    assert issues == []
    assert data["model_calls"] == 8
    assert len(research.get_status(root, run_id)["draft_sections"]) == 7

    review_only = iter(responses[-1:])
    monkeypatch.setattr(writer, "_request_deepseek", lambda **_: {"choices": [{"message": {"content": json.dumps(next(review_only))}, "finish_reason": "stop"}], "usage": {}})
    second, second_issues = writer.write_draft(root, run_id)
    assert second_issues == []
    assert second["model_calls"] == 1
    assert set(second["reused_sections"]) == {*fixed_quality.QUESTION_GROUPS, "synthesis"}


def test_synthesis_prompt_uses_compact_contract_not_full_draft_schema() -> None:
    run = {"ticker": "HK.00728", "as_of": "2026-09-20T00:00:00+00:00"}
    answer = Answer.model_validate({
        "question_id": "Q01", "status": "missing", "statements": [],
        "gap": {"missing": "x", "where_to_find": "y", "next_check": "z", "impact": "w"},
    })

    messages = writer._synthesis_messages(run, [answer], {}, [])

    assert "紧凑字段契约" in messages[0]["content"]
    assert "overturning_evidence 必须是非空字符串" in messages[0]["content"]
    assert "low/medium/high" in messages[0]["content"]
    assert "required_full_draft_schema" not in messages[1]["content"]


def test_synthesis_normalization_marks_model_empty_overturning_evidence_for_review() -> None:
    assert writer._normalize_synthesis({
        "challenge": {"overturning_evidence": ""}, "moats": [{"confidence": "高"}],
    }) == {
        "challenge": {"overturning_evidence": "模型未提供，需人工复核"}, "moats": [{"confidence": "high"}],
    }


def test_review_retries_once_with_compact_json_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path)
    packet = packet_for()
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet)
    valid = {
        "draft_hash": "0" * 64, "status": "passed",
        "checked_questions": list(fixed_quality.QUESTION_IDS),
        "checked_gates": list(fixed_quality.GATE_IDS), "checked_moats": list(fixed_quality.MOAT_IDS), "issues": [],
    }
    contents = iter(['{"issues": [', json.dumps(valid, ensure_ascii=False)])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(writer, "_request_deepseek", lambda **_: {
        "choices": [{"message": {"content": next(contents)}}], "usage": {"total_tokens": 1},
    })

    result, issues = writer.review_draft(root, run_id)

    assert issues == []
    assert result["review_status"] == "passed"


def test_group_parser_accepts_four_consecutive_answer_objects() -> None:
    answers = copy_synthetic_packet()["draft"]["answers"][:4]
    content = "\n".join(json.dumps(answer, ensure_ascii=False) for answer in answers)
    parsed = writer._parse_answers(content, fixed_quality.QUESTION_GROUPS["Q01-Q04"])
    assert [answer.question_id for answer in parsed] == ["Q01", "Q02", "Q03", "Q04"]


def test_group_parser_restores_order_when_all_designated_questions_are_present() -> None:
    answers = copy_synthetic_packet()["draft"]["answers"][:4]
    reversed_content = json.dumps({"answers": list(reversed(answers))}, ensure_ascii=False)

    parsed = writer._parse_answers(reversed_content, fixed_quality.QUESTION_GROUPS["Q01-Q04"])

    assert [answer.question_id for answer in parsed] == ["Q01", "Q02", "Q03", "Q04"]


def test_group_parser_rejects_answered_question_with_a_gap() -> None:
    answers = copy_synthetic_packet()["draft"]["answers"][:4]
    answers[0]["status"] = "answered"
    answers[0]["gap"] = {"missing": "x", "where_to_find": "y", "next_check": "z", "impact": "w"}

    with pytest.raises(writer.WriterError) as error:
        writer._parse_answers(json.dumps({"answers": answers}, ensure_ascii=False), fixed_quality.QUESTION_GROUPS["Q01-Q04"])

    assert error.value.code == "answered_with_gap"


def test_group_answer_cannot_cite_a_locator_outside_its_evidence_pack() -> None:
    answer = Answer.model_validate(copy_synthetic_packet()["draft"]["answers"][0])
    pack = fixed_quality.EvidencePack(
        group_id="Q01-Q04", question_ids=["Q01", "Q02", "Q03", "Q04"], gaps=[],
        chunks=[fixed_quality.EvidenceChunk(
            source_id="11111111-1111-4111-8111-111111111111", locator="page 1",
            source_kind="primary", support_eligible=True, content="教学原文", content_hash="a" * 64,
        )],
    )

    with pytest.raises(writer.WriterError) as error:
        writer._validate_answer_evidence([answer], pack)
    assert error.value.code == "evidence_outside_pack"


def test_group_answer_can_cite_a_trusted_calculation_input_outside_the_pack() -> None:
    answer = Answer.model_validate(copy_synthetic_packet()["draft"]["answers"][0])
    reference = answer.statements[0].evidence[0]
    reference.source_id = "22222222-2222-4222-8222-222222222222"
    reference.locator = "page 9"
    pack = fixed_quality.EvidencePack(group_id="Q01-Q04", question_ids=["Q01", "Q02", "Q03", "Q04"], gaps=[], chunks=[
        fixed_quality.EvidenceChunk(
            source_id="11111111-1111-4111-8111-111111111111", locator="page 1",
            source_kind="primary", support_eligible=True, content="教学原文", content_hash="a" * 64,
        )
    ])
    calculation = SimpleNamespace(source_refs=[EvidenceRef.model_validate({
        "source_id": "22222222-2222-4222-8222-222222222222", "locator": "page 9", "relation": "supports",
    })])

    writer._validate_answer_evidence([answer], pack, [calculation])


def test_group_writer_only_receives_calculations_relevant_to_its_questions() -> None:
    calculations = [
        SimpleNamespace(metric="roe_series"),
        SimpleNamespace(metric="net_profit_yoy_growth"),
        SimpleNamespace(metric="capex_to_revenue"),
    ]

    assert [item.metric for item in writer._calculations_for_group("Q01-Q04", calculations)] == [
        "capex_to_revenue"
    ]
    assert writer._calculations_for_group("Q05-Q08", calculations) == []
    assert [item.metric for item in writer._calculations_for_group("Q09-Q12", calculations)] == [
        "roe_series", "net_profit_yoy_growth", "capex_to_revenue"
    ]


def test_trusted_capex_series_rejects_a_monotonic_decline_claim_when_it_rebounds() -> None:
    packet = packet_for()
    packet.draft.answers[2].statements[0].text = "资本开支/收入比逐步降至较低水平。"
    calculation = SimpleNamespace(
        metric="capex_to_revenue",
        values=[
            {"period": "2021/FY", "value": Decimal("19.73")},
            {"period": "2022/FY", "value": Decimal("19.22")},
            {"period": "2023/FY", "value": Decimal("19.25")},
        ],
        source_refs=[EvidenceRef.model_validate({
            "source_id": "11111111-1111-4111-8111-111111111111", "locator": "page 1", "relation": "supports",
        })],
    )

    review = writer._append_trusted_numeric_review_issues(
        passing_review(packet), packet.draft, [calculation]
    )

    assert review.status == "failed"
    assert review.issues[-1].target == "Q03-trusted-capex-trend"


def test_reviewer_self_contradiction_and_explicit_gap_are_not_errors() -> None:
    packet = packet_for()
    packet.draft.answers[0].statements[0].id = "S1"
    packet.draft.answers[0].statements[0].text = "按可信计算，2025年资本开支/收入为15.18%。"
    packet.draft.answers[0].statements[0].numbers = [NumericFact.model_validate({
        "value": "15.18", "unit": "%", "currency": None, "period": "2025/FY",
        "basis": "capex/revenue*100", "evidence": [{
            "source_id": "11111111-1111-4111-8111-111111111111",
            "locator": "page 9", "relation": "supports",
        }],
    })]
    packet.draft.answers[1].statements[0].text = "2022 年为 6.13%，2023 年为 6.91%，2024 年 ROE 为 7.30%。"
    packet.draft.answers[16].status = "missing"
    packet.draft.answers[16].statements = []
    packet.draft.answers[16].gap = Gap(
        missing="缺证据", where_to_find="年报", next_check="下次", impact="无法判断"
    )
    review = DraftReview.model_validate({
        "draft_hash": fixed_quality.draft_hash(packet.draft), "status": "failed",
        "checked_questions": list(fixed_quality.QUESTION_IDS),
        "checked_gates": list(fixed_quality.GATE_IDS), "checked_moats": list(fixed_quality.MOAT_IDS),
        "issues": [
            {"category": "numeric_consistency", "target": "Q01", "severity": "error", "message": "数值一致，无实质冲突", "evidence": [], "revision_instruction": "无需修改"},
            {"category": "numeric_consistency", "target": "Q02", "severity": "error", "message": "该金额与披露一致", "evidence": [], "revision_instruction": "核对金额"},
            {"category": "numeric_consistency", "target": "Q02", "severity": "error", "message": "四舍五入应为 7.30%，请统一精度", "evidence": [], "revision_instruction": "统一精度"},
            {"category": "numeric_consistency", "target": "Q02", "severity": "error", "message": "称未列 2022 至 2024 年数值", "evidence": [], "revision_instruction": "补列年份"},
            {"category": "evidence_support", "target": "Q01-S1", "severity": "error", "message": "按可信计算的比例未列示分母收入，数值链不完整", "evidence": [], "revision_instruction": "补充分母"},
            {"category": "cross_section_consistency", "target": "Q01-S1", "severity": "error", "message": "同样缺少资本开支绝对额，数值链不完整", "evidence": [], "revision_instruction": "补充绝对额"},
            {"category": "question_alignment", "target": "Q17", "severity": "error", "message": "statements 为空", "evidence": [], "revision_instruction": "补充回答"},
        ],
    })
    normalized = writer._normalize_review(review, packet.draft)
    assert normalized.status == "passed"
    assert all(issue.severity == "warning" for issue in normalized.issues)


def test_answered_question_with_a_gap_cannot_pass_packet_validation() -> None:
    packet = packet_for()
    packet.draft.answers[0].gap = Gap(
        missing="缺证据", where_to_find="年报", next_check="下次", impact="无法判断"
    )

    issues = research.validate_packet(packet)

    assert [(issue.code, issue.location) for issue in issues] == [
        ("answered_with_gap", "draft.answers[0]")
    ]
    assert research.derive_quality_status(packet.draft) == "evidence_incomplete"


def test_model_call_budget_stops_before_fifteenth_call(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def request(**_: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(writer, "_request_deepseek", request)
    tracker = {"calls": 14, "attempts": [], "usage": {}}
    with pytest.raises(writer.WriterError) as exc_info:
        writer._tracked_request(
            tracker, api_key="test", model="test", messages=[], temperature=0.0,
            thinking="disabled", kind="overflow",
        )
    assert exc_info.value.code == "call_budget_exhausted"
    assert called is False
