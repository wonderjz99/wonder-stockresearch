from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import agent, futu_fundamentals, storage
from wonder_stockresearch.fixed import research
from wonder_stockresearch.interactive import analysis_workspace
from wonder_stockresearch.models import (
    EvidenceRef,
    InteractiveQuestion,
    MemoRequest,
    ResearchPacket,
    SourceRequest,
)


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def statements_payload() -> dict[str, object]:
    return futu_fundamentals._dataset(
        "statements",
        {
            "statements": {
                "1": {
                    "structure_list": [{"field_id": 5001, "display_name": "净利润"}],
                    "report_list": [
                        {
                            "date_time_str": "2025-12-31",
                            "period_text": "2025/FY",
                            "currency_code": "CNY",
                            "accounting_standards": "IFRS",
                            "item_list": [{"field_id": 5001, "data": 120.0}],
                        },
                        {
                            "date_time_str": "2024-12-31",
                            "period_text": "2024/FY",
                            "currency_code": "CNY",
                            "accounting_standards": "IFRS",
                            "item_list": [{"field_id": 5001, "data": 100.0}],
                        },
                    ],
                }
            }
        },
        available_from="2025-12-31",
        currency="CNY",
        accounting_standard="IFRS",
    )


class Fundamentals:
    name = "futu"

    def fetch(self, dataset: str, ticker: str) -> dict[str, object]:
        if dataset == "statements":
            return statements_payload()
        return futu_fundamentals._dataset(dataset, {})

    def close(self) -> None:
        pass


def financial_run(root: Path) -> tuple[str, str]:
    run_id = str(
        research.start_run(root, "SYNTHETIC", as_of=datetime(2026, 2, 1, tzinfo=UTC))["run_id"]
    )
    research.collect(root, run_id, "fundamentals", fundamentals_provider=Fundamentals())
    source = next(
        item
        for item in research.list_sources(root, run_id)["sources"]
        if item["metadata"].get("dataset") == "statements"
    )
    return run_id, str(source["source_id"])


def final(source_id: str) -> dict[str, object]:
    return {
        "type": "final",
        "answer": "已按归档财务数据完成临时分析。",
        "claims": [
            {
                "text": "净利润同比计算使用了归档的年度数据。",
                "evidence": [
                    {"source_id": source_id, "locator": "$.payload", "relation": "supports"}
                ],
            }
        ],
        "evidence_refs": [],
        "counterarguments": [],
        "gaps": [],
        "suggested_next_checks": [],
    }


def test_agent_saves_scratchpad_episode_and_trusted_memo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    run_id, source_id = financial_run(root)
    decisions = iter(
        [
            (
                {
                    "type": "tool_call",
                    "tool": "run_trusted_calculation",
                    "arguments": {"metric": "net_profit_yoy_growth"},
                },
                20,
            ),
            (final(source_id), 20),
        ]
    )
    monkeypatch.setattr(agent, "_api_call_for_root", lambda root, messages, model: next(decisions))

    result = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="最近净利润增长率如何？", run_id=run_id),
    )
    session_id = str(result["session_id"])
    status = agent.get_status(root, session_id)
    assert status["scratchpad"]["revision"] >= 3
    assert status["calculation_records"][0]["metric"] == "net_profit_yoy_growth"
    memories = agent.memory_search(root, "SYNTHETIC", "利润")["memories"]
    assert any(item["type"] == "episode" and item["status"] == "provisional" for item in memories)

    agent.memo_save(
        root,
        session_id,
        MemoRequest(
            title="利润趋势",
            content="下一次年报后复核净利润趋势。",
            source_refs=[
                EvidenceRef(source_id=source_id, locator="$.payload", relation="supports")
            ],
        ),
    )
    memories = agent.memory_search(root, "SYNTHETIC", "利润趋势")["memories"]
    assert any(item["type"] == "memo" and item["status"] == "trusted" for item in memories)


def test_analysis_workspace_only_executes_validated_code_on_exported_dataset(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    run_id, _source_id = financial_run(root)
    with storage.project_lock(root), storage.open_db(root) as connection:
        session_id = "00000000-0000-4000-8000-000000000001"
        storage.insert_agent_session(
            connection,
            session_id=session_id,
            ticker="SYNTHETIC",
            run_id=run_id,
            question_json=InteractiveQuestion(question="计算利润", run_id=run_id).model_dump_json(),
            status="active",
            provider="deepseek",
            model="test",
            max_iterations=3,
            token_budget=1000,
            created_at=storage.now_utc(),
        )
        row = storage.get_agent_session(connection, session_id)
    assert row is not None

    dataset = analysis_workspace.create_dataset(root, row, "净利润", limit=20)
    executed = analysis_workspace.run_code(
        root,
        row,
        str(dataset["analysis_id"]),
        'values = [float(item["value"]) for item in data]\nresult = {"count": len(values), "total": sum(values)}',
    )
    assert executed["result"] == {"count": 2, "total": 220.0}
    assert (
        analysis_workspace.read_result(root, row, str(dataset["analysis_id"]))["status"]
        == "succeeded"
    )
    with pytest.raises(analysis_workspace.AnalysisWorkspaceError) as error:
        analysis_workspace.validate_code("import os\nresult = 1")
    assert error.value.code == "analysis_code_not_allowed"
    workspace = root / "data" / "tmp" / "analysis" / session_id / str(dataset["analysis_id"])
    assert json.loads((workspace / "result.json").read_text(encoding="utf-8"))["total"] == 220.0
    analysis_workspace.create_dataset(root, row, "净利润", limit=20)
    analysis_workspace.create_dataset(root, row, "净利润", limit=20)
    with pytest.raises(analysis_workspace.AnalysisWorkspaceError) as error:
        analysis_workspace.create_dataset(root, row, "净利润", limit=20)
    assert error.value.code == "analysis_execution_limit"


def test_official_numeric_conflict_blocks_new_run_finish_validation(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    run_id, _source_id = financial_run(root)
    official = tmp_path / "official.txt"
    official.write_text("官方披露数值。", encoding="utf-8")
    research.source_add(
        root,
        run_id,
        SourceRequest(
            kind="primary",
            title="官方年度披露",
            file=str(official),
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
            publication_precision="datetime",
            metadata={
                "financial_facts": [
                    {"field_id": 5001, "period": "2025/FY", "currency": "CNY", "value": "121"}
                ]
            },
        ),
    )
    status = research.get_status(root, run_id)["source_reconciliation"]
    assert status["conflicts"] == 1
    with storage.open_db(root) as connection:
        conflicts = storage.list_source_conflicts(connection, run_id)
    conflict = next(item for item in conflicts if item["status"] == "conflict")
    assert conflict["official_value"] == "121"
    packet = ResearchPacket.model_validate(copy_synthetic_packet())
    research.save_draft(root, run_id, packet)
    assert "unresolved_source_conflict" in {
        item.code for item in research.validate_run(root, run_id)
    }


def test_reconciliation_keeps_same_metric_period_in_different_currencies_separate(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    run_id = str(
        research.start_run(root, "SYNTHETIC", as_of=datetime(2026, 2, 1, tzinfo=UTC))["run_id"]
    )
    payload = futu_fundamentals._dataset(
        "statements",
        {
            "statements": {
                "1": {
                    "structure_list": [{"field_id": 5001, "display_name": "净利润"}],
                    "report_list": [
                        {
                            "period_text": "2025/FY",
                            "currency_code": "CNY",
                            "item_list": [{"field_id": 5001, "data": 120.0}],
                        },
                        {
                            "period_text": "2025/FY",
                            "currency_code": "HKD",
                            "item_list": [{"field_id": 5001, "data": 130.0}],
                        },
                    ],
                }
            }
        },
        available_from="2025-12-31",
        currency="CNY",
        accounting_standard="IFRS",
    )

    class MultiCurrencyFundamentals:
        name = "futu"

        def fetch(self, dataset: str, ticker: str) -> dict[str, object]:
            return payload if dataset == "statements" else futu_fundamentals._dataset(dataset, {})

        def close(self) -> None:
            pass

    research.collect(
        root, run_id, "fundamentals", fundamentals_provider=MultiCurrencyFundamentals()
    )
    with storage.open_db(root) as connection:
        conflicts = storage.list_source_conflicts(connection, run_id)
    matching = [
        item for item in conflicts if item["metric"] == "5001" and item["period"] == "2025/FY"
    ]
    assert len(matching) == 2
    assert {item["currency"] for item in matching} == {"CNY", "HKD"}
    assert len({item["id"] for item in matching}) == 2


def test_search_discovery_source_cannot_become_core_support() -> None:
    packet = copy_synthetic_packet()
    packet["sources"][0]["metadata"] = {"source_rank": "search_discovery"}
    issues = research.validate_packet(ResearchPacket.model_validate(packet))
    assert "ineligible_as_of_support" in {item.code for item in issues}


def test_scratchpad_compresses_tool_context_without_saving_hidden_reasoning(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    storage.initialize(root)
    session_id = "00000000-0000-4000-8000-000000000002"
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.ensure_stock(connection, "SYNTHETIC", storage.now_utc())
        storage.insert_agent_session(
            connection,
            session_id=session_id,
            ticker="SYNTHETIC",
            run_id=None,
            question_json=InteractiveQuestion(question="压缩上下文").model_dump_json(),
            status="active",
            provider="deepseek",
            model="test",
            max_iterations=1,
            token_budget=1000,
            created_at=storage.now_utc(),
        )
    messages = [{"role": "system", "content": "规则"}, {"role": "user", "content": "x" * 29000}]
    compacted = agent._compress_messages(root, session_id, "压缩上下文", messages)
    assert len(compacted) == 2
    assert agent.get_status(root, session_id)["scratchpad"]["compression_count"] == 1
