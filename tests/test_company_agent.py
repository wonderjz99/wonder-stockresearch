"""公司范围接入：虚构离线资料与 scripted 模型，不调用 OpenD/真实模型。"""

from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from tests.test_agent_data import tool_result
from tests.test_company_data import register, statements
from tests.test_storage import make_root
from wonder_stockresearch import agent, company_data, futu_fundamentals, skill_registry, storage
from wonder_stockresearch.fixed import research
from wonder_stockresearch.interactive import (
    agent_company,
    agent_data,
    agent_memory,
    agent_tools,
    query_workspace,
)
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import (
    EvidenceRef,
    InteractiveFollowup,
    InteractiveQuestion,
    MemoRequest,
)


def turn_row(root: Path, *, network: bool = True, context: str = "company_latest") -> dict:
    storage.initialize(root)
    session_id, turn_id = str(uuid4()), str(uuid4())
    question = InteractiveQuestion(question="测试公司资料", context=context, allow_network=network)
    now = storage.now_utc()
    with (
        storage.project_lock(root),
        storage.open_db(root) as connection,
        storage.transaction(connection),
    ):
        storage.watch_stock(connection, "US.MU", "虚构公司", now)
        storage.insert_agent_session(
            connection,
            session_id=session_id,
            ticker="US.MU",
            run_id=None,
            question_json=question.model_dump_json(),
            status="active",
            provider="deepseek",
            model="test",
            max_iterations=6,
            token_budget=30000,
            created_at=now,
        )
        agent._insert_turn(
            connection,
            turn_id=turn_id,
            session_id=session_id,
            ticker="US.MU",
            run_id=None,
            question_json=question.model_dump_json(),
            skill_audit=None,
            tool_names=None,
            provider="deepseek",
            model="test",
            max_iterations=6,
            token_budget=30000,
            created_at=now,
        )
        row = storage.get_agent_session(connection, session_id)
    return {**row, "turn_id": turn_id}


def newer(root: Path, value: object, previous) -> object:
    source = register(root, "statements", statements(value))
    source = source.model_copy(
        update={"retrieved_at": previous.retrieved_at + timedelta(seconds=1)}
    )
    with storage.open_db(root) as connection:
        storage.upsert_source(connection, research._source_row(source))
    return source


def query(root: Path, row: dict, **kwargs) -> dict:
    return agent_company.query(
        root, row, dataset=None, query="", period=None, mode="facts", limit=200, **kwargs
    )


def test_latest_pins_one_source_per_dataset_and_retries_do_not_reselect(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    first = register(root, "statements", statements(10))
    second = newer(root, 20, first)
    row = turn_row(root)
    before = query(root, row)
    assert {item["source_id"] for item in before["records"]} == {str(second.id)}
    assert (
        next(item for item in before["records"] if item["field_key"].endswith(":data"))[
            "value_decimal"
        ]
        == "20"
    )
    third = newer(root, 30, second)
    assert query(root, row) == before
    assert {item["source_id"] for item in query(root, turn_row(root))["records"]} == {str(third.id)}
    with storage.open_db(root) as connection:
        frozen = storage.get_agent_turn(connection, row["turn_id"])["question_json"]
    assert str(second.id) in frozen and str(third.id) not in frozen


def test_profile_sql_receipts_and_memo_require_current_turn_usage(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    source = register(
        root,
        "profile",
        [{"name": "name", "value": "虚构名称"}, {"name": "business", "value": "离线夹具"}],
    )
    row = turn_row(root)
    fields = agent_company.query(
        root, row, dataset="profile", query="name", period=None, mode="fields", limit=20
    )
    assert fields["records"] and "locator" not in fields["records"][0]
    assert (
        query_workspace.record_tool_evidence_receipts(
            root, row, row["turn_id"], "query_company_data", fields
        )
        == 0
    )
    schema = query_workspace.describe_schema(root, row, row["turn_id"])
    assert schema["snapshot_schema_version"] == 2 and schema["profile_count"] == 2
    result = query_workspace.run_readonly_sql(
        root,
        row,
        row["turn_id"],
        "SELECT fact_id,value_json FROM company_profile_facts WHERE label='name'",
    )
    assert json.loads(result["rows"][0]["value_json"]) == "虚构名称"
    ref = EvidenceRef.model_validate(
        {key: result["citable_refs"][0][key] for key in ("source_id", "locator", "relation")}
    )
    assert str(ref.source_id) == str(source.id)
    with pytest.raises(AgentError, match="回执"):
        agent._validate_evidence_refs(root, row, [ref], turn_id=row["turn_id"])
    assert (
        query_workspace.record_tool_evidence_receipts(
            root, row, row["turn_id"], "run_readonly_sql", result
        )
        == 1
    )
    agent._validate_evidence_refs(root, row, [ref], turn_id=row["turn_id"])
    agent._validate_memo_sources(
        root, row, MemoRequest(title="夹具", content="夹具", source_refs=[ref]), row["turn_id"]
    )
    other = turn_row(root)
    with pytest.raises(AgentError, match="回执"):
        agent._validate_evidence_refs(root, other, [ref], turn_id=other["turn_id"])


class ProfileProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = 0

    def fetch(self, dataset: str, ticker: str) -> dict:
        assert dataset == "profile" and ticker == "US.MU"
        self.calls += 1
        return {"dataset": dataset, "payload": [{"name": "name", "value": "补取的虚构名称"}]}

    def close(self) -> None:
        self.closed += 1


def test_missing_profile_fetch_indexes_and_new_turn_reuses_without_provider(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    row = turn_row(root)
    schema = query_workspace.describe_schema(root, row, row["turn_id"])
    assert schema["fact_count"] == schema["profile_count"] == 0
    provider = ProfileProvider()
    monkeypatch.setattr(futu_fundamentals, "make_provider", lambda: provider)
    fetched = agent_data.fetch_financial_data(root, row, "profile", "", limit=20)
    assert fetched["records"][0]["value_json"] == '"补取的虚构名称"'
    assert company_data.status(root)["coverage"]
    cached = agent_data.fetch_financial_data(root, row, "profile", "", limit=20)
    assert cached["cached"] and provider.calls == provider.closed == 1
    new_row = turn_row(root)
    reused = query(root, new_row)
    assert reused["records"][0]["source_id"] == fetched["fetched_source_id"]
    assert provider.calls == 1
    # 旧范围不会因此获得其他 Turn 的补取资料。
    legacy = turn_row(root, context="latest_run")
    with storage.open_db(root) as connection:
        assert fetched["fetched_source_id"] not in {
            item["id"] for item in agent_company.context_sources(connection, legacy)
        }


def test_offline_followup_inherits_permissions_and_selects_new_scope(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    first = register(root, "statements", statements(10))
    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        lambda *_args: (
            {
                "type": "final",
                "answer": "夹具回答",
                "claims": [],
                "evidence_refs": [],
                "counterarguments": [],
                "gaps": [],
                "suggested_next_checks": [],
            },
            20,
        ),
    )
    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="夹具", context="company_latest", allow_network=False),
    )
    second = newer(root, 20, first)
    followup = agent.followup(root, result["session_id"], InteractiveFollowup(question="新问题"))
    with storage.open_db(root) as connection:
        row = storage.get_agent_session(connection, result["session_id"])
        turn = storage.get_agent_turn(connection, followup["turn_id"])
    payload = json.loads(turn["question_json"])
    assert payload["allow_network"] is False and str(second.id) in turn["question_json"]
    monkeypatch.setattr(
        futu_fundamentals, "make_provider", lambda: pytest.fail("离线不能连接供应商")
    )
    with pytest.raises(Exception, match="离线"):
        agent_data.fetch_financial_data(
            root, {**row, "turn_id": followup["turn_id"]}, "profile", "", limit=10
        )


@pytest.mark.parametrize("damage", ["raw", "identity", "other_ticker"])
def test_changed_sources_are_rejected_without_falling_back(tmp_path: Path, damage: str) -> None:
    root = make_root(tmp_path)
    source = register(root, "statements", statements(10))
    row = turn_row(root)
    query(root, row)
    if damage == "raw":
        (root / source.raw_path).write_bytes(b"changed")
    else:
        with storage.open_db(root) as connection:
            if damage == "identity":
                connection.execute(
                    "UPDATE sources SET sha256=? WHERE id=?", ("0" * 64, str(source.id))
                )
            else:
                storage.watch_stock(connection, "US.OTHER", "另一虚构公司", storage.now_utc())
                connection.execute(
                    "UPDATE sources SET ticker='US.OTHER' WHERE id=?", (str(source.id),)
                )
    with pytest.raises(AgentError):
        query(root, row)


def test_failed_latest_batch_and_unknown_units_are_visible(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    first = register(root, "statements", statements(10))
    source = newer(root, "--", first)
    row = turn_row(root)
    result = query(root, row)
    coverage = next(item for item in result["coverage"] if item["dataset"] == "statements")
    assert coverage["status"] == "partial" and coverage["diagnostics"]
    assert {item["source_id"] for item in result["records"]} == {str(source.id)}
    assert all(item["value_decimal"] != "0" and item["unit"] is None for item in result["records"])
    # 无法识别的最新形状应报告 failed，不静默使用更旧的有效批次。
    bad = register(root, "statements", {"unknown_shape": []})
    bad = bad.model_copy(update={"retrieved_at": source.retrieved_at + timedelta(seconds=1)})
    with storage.open_db(root) as connection:
        storage.upsert_source(connection, research._source_row(bad))
    result = query(root, turn_row(root))
    assert result["records"] == []
    assert (
        next(item for item in result["coverage"] if item["dataset"] == "statements")["status"]
        == "failed"
    )


def test_superseded_inputs_remain_citable_and_backup_restores_calculation(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    first = register(root, "statements", statements(10))
    row = turn_row(root)
    sql = "SELECT fact_id,value_decimal,period FROM financial_facts WHERE field_key='statements:1:9001:data'"
    before = query_workspace.run_readonly_sql(root, row, row["turn_id"], sql)
    query_workspace.record_tool_evidence_receipts(
        root, row, row["turn_id"], "run_readonly_sql", before
    )

    class FinancialProvider(ProfileProvider):
        def fetch(self, dataset, ticker):
            self.calls += 1
            return {"dataset": dataset, "payload": statements(30)}

    monkeypatch.setattr(futu_fundamentals, "make_provider", FinancialProvider)
    fetched = agent_data.fetch_financial_data(root, row, "statements", "", limit=20)
    after = query_workspace.run_readonly_sql(root, row, row["turn_id"], sql)
    assert before["rows"][0]["value_decimal"] == "10" and after["rows"][0]["value_decimal"] == "30"
    assert before["dataset_hash"] != after["dataset_hash"]
    ref = EvidenceRef(
        source_id=first.id, locator=before["citable_refs"][0]["locator"], relation="supports"
    )
    agent._validate_evidence_refs(root, row, [ref], turn_id=row["turn_id"])
    function = query_workspace.run_analysis_function(
        root,
        row,
        row["turn_id"],
        after["analysis_id"],
        'def analyze(rows):\n    return {"average": sum([float(row["value_decimal"]) for row in rows]) / len(rows)}',
    )
    assert function["result"] == {"average": 30.0}
    query_workspace.record_tool_evidence_receipts(
        root, row, row["turn_id"], "run_analysis_function", function
    )
    backup = research.backup(root, "data/backups/company-agent")
    directory = root / backup["output"]
    manifest = json.loads((directory / "manifest.json").read_text())
    assert not manifest["missing"] and manifest["company_database"]
    analysis_entries = [item for item in manifest["files"] if item["kind"] == "analysis_artifact"]
    assert len(analysis_entries) == 10  # 两组 SQL 输入/结果与一组函数代码/结果。
    restored = tmp_path / "restored"
    (restored / "data").mkdir(parents=True)
    for name in ("wonder.db", "company.db"):
        shutil.copy2(directory / name, restored / "data" / name)
    shutil.copytree(directory / "files", restored, dirs_exist_ok=True)
    for item in manifest["files"]:
        assert storage.sha256_file(restored / item["path"]) == item["sha256"]
    artifact = query_workspace.read_analysis_artifact(
        restored, row, row["turn_id"], after["analysis_id"]
    )
    assert artifact["status"] == "function_succeeded"
    assert query(restored, row)["records"][0]["source_id"] == fetched["fetched_source_id"]
    agent._validate_evidence_refs(restored, row, [ref], turn_id=row["turn_id"])


def test_actual_agent_invalidates_query_cache_after_fetch(tmp_path: Path, monkeypatch) -> None:
    root = make_root(tmp_path)
    register(root, "statements", statements(10))

    class FinancialProvider(ProfileProvider):
        def fetch(self, dataset, ticker):
            return {"dataset": dataset, "payload": statements(30)}

    monkeypatch.setattr(futu_fundamentals, "make_provider", FinancialProvider)
    calls = 0

    def decide(_root, messages, _model):
        nonlocal calls
        calls += 1
        if calls in {1, 3}:
            return {
                "type": "tool_call",
                "tool": "query_archived_financials",
                "arguments": {"query": "自定义原始科目"},
            }, 20
        if calls == 2:
            assert (
                next(
                    item
                    for item in tool_result(messages)["records"]
                    if item["field_key"].endswith(":data")
                )["value"]
                == "10"
            )
            return {
                "type": "tool_call",
                "tool": "fetch_financial_data",
                "arguments": {"dataset": "statements"},
            }, 20
        result = tool_result(messages)
        assert (
            next(item for item in result["records"] if item["field_key"].endswith(":data"))["value"]
            == "30"
        )
        item = next(item for item in result["records"] if item["field_key"].endswith(":data"))
        ref = {**{key: item[key] for key in ("source_id", "locator")}, "relation": "supports"}
        return {
            "type": "final",
            "answer": "夹具更新为30",
            "claims": [],
            "evidence_refs": [ref],
            "counterarguments": [],
            "gaps": [],
            "suggested_next_checks": [],
        }, 20

    monkeypatch.setattr(agent, "_api_call_for_root", decide)
    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="自定义原始科目", context="company_latest"),
        tool_names=["query_archived_financials", "fetch_financial_data"],
    )
    assert result["status"] == "completed" and calls == 4


def test_company_query_does_not_mutate_research_or_main_schema(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    register(root, "statements", statements())
    row = turn_row(root)
    before = storage.sha256_file(storage.db_path(root))
    result = agent_tools._execute_tool_impl(root, row, "query_company_data", {"mode": "facts"})
    assert result["records"] and storage.sha256_file(storage.db_path(root)) == before
    with sqlite3.connect(storage.db_path(root)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION


def test_catalog_keeps_all_periods_when_fact_preview_is_shortened(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    payload = statements(10)
    reports = payload["statements"]["1"]["report_list"]
    for year in (2025, 2024):
        record = copy.deepcopy(reports[0])
        record.update(period_text=f"{year}/FY", fiscal_year=year)
        reports.append(record)
    register(root, "statements", payload)
    row = turn_row(root)
    result = agent_company.query(
        root, row, dataset="statements", query="自定义原始科目", period=None, mode="facts", limit=20
    )
    projected = json.loads(agent_memory.tool_context(result, token_limit=1000))
    field = next(item for item in projected["field_catalog"] if item["field_key"].endswith(":data"))
    assert field["periods"] == ["2026/FY", "2025/FY", "2024/FY"]
    assert field["fact_count"] == 3
    assert "financial_facts" in projected["sql_tables"]
    # 省略预览不能修改真实结果或丢掉实际可查询的期间。
    assert len(result["records"]) == 6


def test_sql_preview_references_follow_visible_rows_and_function_uses_full_input(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    payload = statements(10)
    reports = payload["statements"]["1"]["report_list"]
    for year, value in ((2025, 20), (2024, 30), (2023, 40), (2022, 50)):
        record = copy.deepcopy(reports[0])
        record.update(period_text=f"{year}/FY", fiscal_year=year)
        record["item_list"][0]["data"] = value
        reports.append(record)
    register(root, "statements", payload)
    row = turn_row(root)
    result = query_workspace.run_readonly_sql(
        root,
        row,
        row["turn_id"],
        "SELECT fact_id,period,value_decimal FROM financial_facts WHERE field_key='statements:1:9001:data' ORDER BY period DESC",
    )
    projected = json.loads(agent_memory.tool_context(result, token_limit=500))
    by_id = {ref["fact_id"]: ref for ref in result["citable_refs"]}
    visible = {by_id[item["fact_id"]]["locator"] for item in projected["rows"]}
    assert visible == {ref["locator"] for ref in projected["citable_refs"]}
    assert projected["rows_omitted"] > 0
    function = query_workspace.run_analysis_function(
        root,
        row,
        row["turn_id"],
        result["analysis_id"],
        'def analyze(rows):\n    return {"count": len(rows), "average": sum([float(row["value_decimal"]) for row in rows]) / len(rows)}',
    )
    assert function["result"] == {"count": 5, "average": 30.0}


def test_all_supported_datasets_are_available_in_company_sql(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    register(root, "profile", [{"name": "业务", "value": "虚构业务"}])
    register(root, "statements", statements(10))
    register(
        root,
        "operational_efficiency",
        {"item_list": [{"period_text": "2025/FY", "employee_num": 100}]},
    )
    register(
        root,
        "revenue_breakdown",
        {
            "period": "2025/FY",
            "breakdown_list": [
                {"type": "BUSINESS", "item_list": [{"name": "虚构业务", "main_oper_income": 42}]}
            ],
        },
    )
    row = turn_row(root)
    result = query_workspace.run_readonly_sql(
        root,
        row,
        row["turn_id"],
        "SELECT fact_id,dataset,field_key,value_decimal,dimensions_json FROM financial_facts",
    )
    assert {item["dataset"] for item in result["rows"]} == {
        "statements",
        "operational_efficiency",
        "revenue_breakdown",
    }
    assert any(
        json.loads(item["dimensions_json"]).get("segment") == "虚构业务" for item in result["rows"]
    )
    assert len(result["citable_refs"]) == result["row_count"]


def test_real_skill_overhead_still_leaves_three_rows_for_model_calculation(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    shutil.copytree(Path(__file__).resolve().parents[1] / "skills", root / "skills")
    payload = statements(10)
    reports = payload["statements"]["1"]["report_list"]
    for year, value in ((2025, 20), (2024, 30)):
        record = copy.deepcopy(reports[0])
        record.update(period_text=f"{year}/FY", fiscal_year=year)
        record["item_list"][0]["data"] = value
        reports.append(record)
    register(root, "statements", payload)
    loaded = skill_registry.load_selected(
        root, ["company-analysis-method", "interactive-research-agent", "analysis-workspace"]
    )
    costs = []
    calls = 0

    def decide(_root, messages, _model):
        nonlocal calls
        calls += 1
        costs.append(agent_memory.estimate_input_tokens(messages) + 100)
        if calls == 1:
            decision = {
                "type": "tool_call",
                "tool": "query_company_data",
                "arguments": {"dataset": "statements"},
            }
        elif calls == 2:
            decision = {
                "type": "tool_call",
                "tool": "run_readonly_sql",
                "arguments": {
                    "sql": "SELECT fact_id,field_key,label,value_decimal,period,period_kind,unit,currency,scale,dimensions_json "
                    "FROM financial_facts WHERE field_key='statements:1:9001:data' ORDER BY period DESC LIMIT 3"
                },
            }
        elif calls == 3:
            result = tool_result(messages)
            assert len(result["rows"]) == 3 and not result.get("rows_omitted")
            decision = {
                "type": "tool_call",
                "tool": "run_analysis_function",
                "arguments": {
                    "analysis_id": result["analysis_id"],
                    "code": 'def analyze(rows):\n    return {"average": sum([float(row["value_decimal"]) for row in rows]) / len(rows)}',
                },
            }
        else:
            result = tool_result(messages)
            assert result["result"]["average"] == 20.0
            decision = {
                "type": "final",
                "answer": "三个期间原始数值平均20，单位未知。",
                "claims": [],
                "evidence_refs": result["source_refs"],
                "counterarguments": [],
                "gaps": ["单位未知"],
                "suggested_next_checks": [],
            }
        return decision, costs[-1]

    monkeypatch.setattr(agent, "_api_call_for_root", decide)
    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="自定义原始科目三个财年平均", context="company_latest"),
        skill_context=skill_registry.execution_context(loaded),
        skill_audit=skill_registry.selection_audit(loaded),
        tool_names=skill_registry.execution_tool_ids(loaded, "interactive_research"),
    )
    assert result["status"] == "completed" and calls == 4
    assert max(costs) > 0 and sum(costs) <= 30000
