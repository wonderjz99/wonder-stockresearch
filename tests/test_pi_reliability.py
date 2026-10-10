"""失败形态回归：虚构归档与离线 native bridge，不访问模型/OpenD。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.test_company_agent import turn_row
from tests.test_company_data import register, statements
from tests.test_storage import make_root
from wonder_stockresearch import agent, storage
from wonder_stockresearch.fixed import research
from wonder_stockresearch.interactive import (
    agent_company,
    agent_memory,
    agent_model,
    agent_tools,
    query_workspace,
)
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.interactive.pi import pi_bridge, pi_context, pi_evidence
from wonder_stockresearch.models import InteractiveFollowup, InteractiveQuestion


def exchange(number: int, tool: str, result: dict, arguments: dict | None = None) -> list[dict]:
    call_id = f"call-{number}"
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "toolCall", "id": call_id, "name": tool, "arguments": arguments or {}}
            ],
        },
        {
            "role": "toolResult",
            "toolCallId": call_id,
            "toolName": tool,
            "isError": False,
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        },
    ]


def test_large_status_is_small_at_handler_and_latest_context_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    row = turn_row(root)
    row["run_id"] = research.start_run(root, "US.MU")["run_id"]
    # 状态投影夹具也使用归属一致的真实 Run/Turn，不能绕过执行边界。
    with storage.open_db(root) as connection:
        connection.execute(
            "UPDATE agent_turns SET run_id=? WHERE id=?", (row["run_id"], row["turn_id"])
        )
        connection.execute(
            "UPDATE agent_sessions SET run_id=? WHERE id=?", (row["run_id"], row["id"])
        )
    huge = {
        "status": "draft",
        "steps": [{"body": "步骤" * 10000}],
        "draft_sections": {"Q01": "草稿" * 5000},
    }
    monkeypatch.setattr(agent_tools.research, "get_status", lambda *_: copy.deepcopy(huge))
    result = agent_tools._execute_tool_impl(root, row, "get_run_status", {})
    assert "steps" not in result and result["steps_summary"]["count"] == 1
    messages = [{"role": "user", "content": "读取公司名称"}, *exchange(1, "get_run_status", huge)]
    projected = pi_context.project(messages, "受控规则", [], 1000)
    assert pi_context.estimate("受控规则", [], projected) <= 1000
    assert len(pi_context.groups(projected)) == 2
    assert "草稿" not in pi_context.text(projected[-1])


def test_query_filters_exact_fields_and_periods_and_searches_chinese_aliases(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    payload = statements(10)
    table = payload["statements"]["1"]
    table["structure_list"][0]["display_name"] = "营业收入"
    for year in (2024, 2025):
        annual = copy.deepcopy(table["report_list"][0])
        annual.update(period_text=f"{year}/FY", fiscal_year=year)
        table["report_list"].append(annual)
    interim = copy.deepcopy(table["report_list"][0])
    interim.update(period_text="2026/H1", financial_type="INTERIM")
    table["report_list"].append(interim)
    register(root, "statements", payload)
    row = turn_row(root)
    args = {
        "dataset": "statements",
        "query": "revenue",
        "period": None,
        "mode": "facts",
        "limit": 1,
        "field_keys": ["statements:1:9001:data"],
        "periods": ["2024/FY", "2025/FY"],
        "period_kind": "annual",
    }
    first = agent_company.query(root, row, **args)
    second = agent_company.query(root, row, **args, offset=first["next_offset"])
    assert first["total_matches"] == 2 and first["has_more"]
    assert second["returned_records"] == 1 and second["next_offset"] is None
    assert {item["period"] for item in first["records"] + second["records"]} == {
        "2024/FY",
        "2025/FY",
    }
    assert all(item["unit"] is None for item in first["records"])


def test_projection_pagination_never_skips_rows_hidden_by_context_limit() -> None:
    records = [
        {
            "fact_id": f"f{i}",
            "period": f"{2000 + i}/FY",
            "value_decimal": str(i),
            "label": "收入",
            "locator": "$.reports[" + str(i) + "].data",
        }
        for i in range(20)
    ]
    offset, seen = 0, []
    while offset < len(records):
        page = json.loads(
            pi_context.tool_projection(
                {
                    "mode": "facts",
                    "records": records[offset:],
                    "offset": offset,
                    "total_matches": 20,
                    "returned_records": 20 - offset,
                    "has_more": False,
                    "next_offset": None,
                },
                token_limit=500,
            )
        )
        assert 0 < page["returned_records"] == len(page["records"]) < 20
        seen.extend(item["fact_id"] for item in page["records"])
        offset += page["returned_records"]
        assert page["next_offset"] == (offset if offset < 20 else None)
    assert seen == [item["fact_id"] for item in records]


def test_working_record_keeps_calculation_and_pending_page_after_old_group_dropped() -> None:
    messages = [
        {"role": "user", "content": "比较两个期间的均值"},
        *exchange(
            1,
            "run_analysis_function",
            {
                "analysis_id": "calculation",
                "status": "function_succeeded",
                "result": {"six_year_mean": 60.235172, "three_year_mean": 58.624322},
            },
            {"analysis_id": "calculation", "code": "def analyze(rows): return {}"},
        ),
        *exchange(
            2,
            "query_company_data",
            {
                "mode": "facts",
                "records": [{"period": "2025/FY", "value_decimal": "58.227439", "unit": None}],
                "has_more": True,
                "next_offset": 1,
            },
        ),
    ]
    memory = json.loads(pi_context.working_memory(messages))
    assert memory["recent_operations"][0]["observed"]["result"]["six_year_mean"] == 60.235172
    assert memory["recent_operations"][1]["observed"]["next_offset"] == 1
    projected = pi_context.project(messages, "规则", [], 250)
    assert len(projected) < len(messages)
    assert messages[1]["content"][0]["id"] == "call-1"  # 原可见审计未被修改


def test_single_oversized_nested_result_is_navigation_not_unbounded_context() -> None:
    projected = pi_context.tool_projection(
        {
            "analysis_id": "artifact",
            "status": "function_succeeded",
            "result": {"nested": "文本" * 50000},
        },
        token_limit=300,
    )
    assert agent_memory.estimate_input_tokens([{"content": projected}]) <= 300
    assert json.loads(projected)["analysis_id"] == "artifact"
    assert json.loads(projected)["context_truncated"] is True


def test_native_citation_repair_uses_receipt_id_without_requery(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_root(tmp_path)
    source = register(root, "profile", [{"name": "公司名称", "value": "虚构公司"}])
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "pi")
    calls, repair = [], []

    def run(_config, callback):
        messages = [{"role": "user", "content": "公司名称"}]

        def finish(content: list[dict], stop: str = "stop"):
            limits = callback("model_start", {"messages": messages})
            calls.append(limits)
            message = {
                "role": "assistant",
                "stopReason": stop,
                "content": content,
                "usage": {"input": 90, "output": 10},
            }
            messages.append(message)
            return callback("model_end", {"usageKnown": True, "consumed": 100, "message": message})

        finish(
            [
                {
                    "type": "toolCall",
                    "id": "c1",
                    "name": "query_company_data",
                    "arguments": {"dataset": "profile", "mode": "facts"},
                }
            ],
            "toolUse",
        )
        response = callback(
            "tool",
            {
                "toolCallId": "c1",
                "name": "query_company_data",
                "arguments": {"dataset": "profile", "mode": "facts"},
            },
        )
        tool_message = {
            "role": "toolResult",
            "toolCallId": "c1",
            "toolName": "query_company_data",
            "content": [{"type": "text", "text": response["content"]}],
            "isError": False,
        }
        messages.append(tool_message)
        callback("message", {"message": tool_message})
        ref = json.loads(response["content"])["records"][0]
        invalid = {
            "type": "final",
            "answer": "虚构公司",
            "evidence_refs": [
                {"source_id": str(source.id), "locator": "$.wrong", "relation": "supports"}
            ],
        }
        repaired = finish([{"type": "text", "text": json.dumps(invalid)}])
        assert "$.wrong" in repaired["repair"] and ref["ref_id"] in repaired["repair"]
        repair.append(repaired["repair"])
        messages.append({"role": "user", "content": repaired["repair"]})
        projected = callback("context", {"messages": messages})
        assert any("本 Turn 工作记录" in pi_context.text(item) for item in projected)
        final = {
            "type": "final",
            "answer": "虚构公司",
            "evidence_refs": [{"ref_id": ref["ref_id"]}],
        }
        finish([{"type": "text", "text": json.dumps(final)}])
        callback("settled", {})

    monkeypatch.setattr(pi_bridge, "run", run)
    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="公司名称", context="company_latest"),
        tool_names=["query_company_data"],
    )
    assert [item["repair_only"] for item in calls] == [False, False, True]
    assert result["report"]["evidence_refs"][0]["source_id"] == str(source.id)
    assert "ref_id" not in result["report"]["evidence_refs"][0]
    status = agent.get_status(root, result["session_id"])["turns"][0]
    assert status["used_tokens"] == 300 and status["iteration"] == 3
    assert repair


def test_reference_id_cannot_authorize_another_turn_or_mismatched_locator(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    register(root, "profile", [{"name": "公司名称", "value": "虚构公司"}])
    row = turn_row(root)
    result = agent_company.query(
        root, row, dataset="profile", query="", period=None, mode="facts", limit=5
    )
    query_workspace.record_tool_evidence_receipts(
        root, row, row["turn_id"], "query_company_data", result
    )
    available = pi_evidence.catalog(root, row["turn_id"])
    ref_id = next(iter(available))
    raw = {"type": "final", "answer": "夹具", "evidence_refs": [{"ref_id": ref_id}]}
    with pytest.raises(AgentError, match="当前 Turn"):
        pi_evidence.expand_decision(raw, pi_evidence.catalog(root, turn_row(root)["turn_id"]))
    raw["evidence_refs"][0]["locator"] = "$.wrong"
    with pytest.raises(AgentError, match="不一致"):
        pi_evidence.expand_decision(raw, available)


def test_explicit_old_budget_is_preserved_across_response_and_followup(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "pi")
    question = InteractiveQuestion(
        question="原问题",
        max_iterations=6,
        token_budget=30000,
        input_token_limit=8000,
        output_token_limit=512,
    )
    result = agent.ask(
        tmp_path,
        "SYNTHETIC",
        question,
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "ask_user", "question": "期间？"}, 10)]
        ),
    )
    with storage.open_db(tmp_path) as connection:
        before = storage.get_agent_turn(connection, result["turn_id"])["budget_json"]
    agent.respond(
        tmp_path,
        result["session_id"],
        result["turn_id"],
        InteractiveFollowup(question="2025"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "final", "answer": "夹具"}, 10)]
        ),
    )
    followup = agent.followup(
        tmp_path,
        result["session_id"],
        InteractiveFollowup(question="下一问"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "final", "answer": "夹具"}, 10)]
        ),
    )
    with storage.open_db(tmp_path) as connection:
        assert storage.get_agent_turn(connection, result["turn_id"])["budget_json"] == before
        assert storage.get_agent_turn(connection, followup["turn_id"])["budget_json"] == before
    assert InteractiveQuestion(question="新问题").token_budget == 60000
    assert InteractiveQuestion(question="新问题").max_iterations == 10


def test_native_single_request_limit_stops_before_reserving_or_consuming_budget(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "pi")

    def run(_config, callback):
        callback("model_start", {"messages": [{"role": "user", "content": "输入" * 2000}]})
        pytest.fail("超过单轮输入上限后不应发起模型调用")

    monkeypatch.setattr(pi_bridge, "run", run)
    with pytest.raises(AgentError) as error:
        agent.ask(
            tmp_path, "SYNTHETIC", InteractiveQuestion(question="问题", input_token_limit=1024)
        )
    assert error.value.code == "agent_context_too_large"
    with storage.open_db(tmp_path) as connection:
        turn = dict(connection.execute("SELECT * FROM agent_turns").fetchone())
    assert turn["used_tokens"] == turn["iteration"] == 0


def test_function_returns_period_coverage_and_programmed_interval_relationship(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    payload = statements(58)
    reports = payload["statements"]["1"]["report_list"]
    for year, value in ((2025, 59), (2024, 64)):
        record = copy.deepcopy(reports[0])
        record.update(period_text=f"{year}/FY", fiscal_year=year)
        record["item_list"][0]["data"] = value
        reports.append(record)
    register(root, "statements", payload)
    row = turn_row(root)
    sql = query_workspace.run_readonly_sql(
        root,
        row,
        row["turn_id"],
        "SELECT fact_id,field_key,label,value_decimal,period,unit,scale,currency FROM financial_facts "
        "WHERE field_key='statements:1:9001:data' ORDER BY period",
    )
    result = query_workspace.run_analysis_function(
        root,
        row,
        row["turn_id"],
        sql["analysis_id"],
        "def analyze(rows):\n"
        "    all_values = [float(r['value_decimal']) for r in rows]\n"
        "    recent = [float(r['value_decimal']) for r in rows if r['period'] != '2024/FY']\n"
        "    return {'mean': sum(all_values)/len(all_values),\n"
        "        'recent_contained': min(all_values) <= min(recent) and max(recent) <= max(all_values)}",
    )
    assert result["result"]["recent_contained"] is True
    assert result["input_summary"]["row_count"] == 3
    assert result["input_summary"]["series"][0]["periods"] == ["2024/FY", "2025/FY", "2026/FY"]
    assert result["input_summary"]["series"][0]["unit"] is None
    assert len(result["function_hash"]) == 64
    artifact = query_workspace.read_analysis_artifact(root, row, row["turn_id"], sql["analysis_id"])
    projected = json.loads(pi_context.tool_projection(artifact))
    assert projected["result"]["recent_contained"] is True
    assert projected["sql_rows_archived"] is True
