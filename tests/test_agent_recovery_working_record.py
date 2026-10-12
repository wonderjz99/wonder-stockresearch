"""离线执行复盘形态：虚构财务资料、脚本模型，覆盖两种真实循环。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.test_company_agent import turn_row
from tests.test_company_data import register, statements
from tests.test_stage19_query_workspace import _turn_row
from tests.test_storage import make_root
from wonder_stockresearch import agent, chat, storage
from wonder_stockresearch.interactive import (
    agent_company,
    agent_context,
    agent_execution,
    agent_memory,
    agent_model,
    agent_working_record,
    query_workspace,
)
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import InteractiveQuestion, RoutePlan


def metric_archive(root: Path, count: int = 4) -> None:
    payload = statements(10)
    statement = payload["statements"]["1"]
    statement["structure_list"][0]["display_name"] = "净资产收益率 ROE"
    base = statement["report_list"][0]
    statement["report_list"] = []
    for i in range(count):
        row = copy.deepcopy(base)
        row.update(period_text=f"{2025 - i}/FY", fiscal_year=2025 - i)
        row["item_list"][0]["data"] = 10 * (i + 1)
        statement["report_list"].append(row)
    register(root, "statements", payload)


def last_result(messages: list[dict]) -> dict:
    for message in reversed(messages):
        content = message["content"]
        for marker in (" 返回：", " 返回可恢复错误："):
            if marker in content:
                return json.JSONDecoder().raw_decode(content.split(marker, 1)[1])[0]
    raise AssertionError("没有工具结果")


def decision(tool: str, arguments: dict) -> tuple[dict, int]:
    return {"type": "tool_call", "tool": tool, "arguments": arguments}, 10


@pytest.mark.parametrize(
    "key",
    [
        "lambda row: row['period']",
        "lambda row: float(row['value_decimal'])",
        "lambda row: (row['period'], -float(row['value_decimal']))",
    ],
)
def test_sort_lambda_runs_in_restricted_subprocess(tmp_path: Path, key: str) -> None:
    root = make_root(tmp_path)
    row, tid = _turn_row(root)
    result = query_workspace.run_readonly_sql(
        root,
        row,
        tid,
        "SELECT fact_id,period,value_decimal FROM financial_facts ORDER BY period DESC",
    )
    output = query_workspace.run_analysis_function(
        root,
        row,
        tid,
        result["analysis_id"],
        f"def analyze(rows):\n    ordered = sorted(rows, key={key})\n    return {{'periods': [r['period'] for r in ordered]}}",
    )
    assert output["result"]["periods"] == ["2024/FY", "2025/FY"]


@pytest.mark.parametrize(
    "expression",
    [
        "(lambda r: r)(rows)",
        "sorted(rows, key=lambda a,b: a)",
        "sorted(rows, key=lambda a=rows: a)",
        "sorted(rows, key=lambda r: rows[0])",
        'sorted(rows, key=lambda r: r.get("period"))',
        "sorted(rows, key=lambda r: sum(r))",
        "sorted(rows, key=lambda r: (lambda a: a)(r))",
        "sorted(rows, key=lambda r: [a for a in r])",
        "sorted(rows, key=lambda r: (a := r))",
        "sorted(rows, key=lambda r: float)",
        "sorted(rows, key=lambda float: float)",
    ],
)
def test_unsupported_lambda_is_rejected_with_location(expression: str) -> None:
    with pytest.raises(query_workspace.QueryWorkspaceError) as failure:
        query_workspace.validate_function(
            f'def analyze(rows):\n    return {{"result": {expression}}}'
        )
    assert failure.value.code == "analysis_function_not_allowed"
    assert failure.value.node and failure.value.line == 2 and failure.value.column


@pytest.mark.parametrize(
    "code",
    [
        "def analyze(rows):\n    sorted = rows\n    return {}",
        "def analyze(rows):\n    import os\n    return {}",
        'def analyze(rows):\n    return {"x": rows.__class__}',
        'def analyze(rows):\n    return {"x": open("test")}',
        "def analyze(rows):\n    def helper():\n        return 1\n    return {}",
    ],
)
def test_existing_restrictions_still_apply(code: str) -> None:
    with pytest.raises(query_workspace.QueryWorkspaceError):
        query_workspace.validate_function(code)


def test_all_declared_functions_exist_and_failures_consume_execution_quota(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    row, tid = _turn_row(root)
    result = query_workspace.run_readonly_sql(root, row, tid, "SELECT fact_id FROM financial_facts")
    output = query_workspace.run_analysis_function(
        root,
        row,
        tid,
        result["analysis_id"],
        """def analyze(rows):
    pairs = [pair for pair in enumerate(range(int('2')))]
    return {'value': str(round(abs(float('-2.4')))), 'total': sum([min([1, 2]), max([1, 2]), len(sorted(pairs))])}
""",
    )
    assert output["result"] == {"value": "2", "total": 5}
    for _ in range(2):
        result = query_workspace.run_readonly_sql(
            root, row, tid, "SELECT fact_id FROM financial_facts"
        )
        with pytest.raises(query_workspace.QueryWorkspaceError, match="执行失败"):
            query_workspace.run_analysis_function(
                root,
                row,
                tid,
                result["analysis_id"],
                'def analyze(rows):\n    return {"value": 1 / 0}',
            )
    result = query_workspace.run_readonly_sql(root, row, tid, "SELECT fact_id FROM financial_facts")
    with pytest.raises(query_workspace.QueryWorkspaceError) as failure:
        query_workspace.run_analysis_function(
            root, row, tid, result["analysis_id"], "def analyze(rows):\n    return {}"
        )
    assert failure.value.code == "analysis_function_limit"


def test_cross_dataset_catalog_is_compact_and_full_facts_remain_available(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    metric_archive(root, 12)
    row = turn_row(root, network=False)
    directory = agent_company.query(
        root, row, dataset=None, query="ROE", period=None, mode="fields", limit=1
    )
    field = directory["records"][0]
    assert field["dataset"] == "statements"
    assert field["period_count"] == 12 and len(field["periods"]) == 5
    assert field["periods_omitted"] == 7 and field["earliest_period"] == "2014/FY"
    assert field["latest_period"] == "2025/FY" and directory["next_offset"] == 1
    other_page = agent_company.query(
        root, row, dataset=None, query="净资产收益率", period=None, mode="fields", limit=1, offset=1
    )
    assert other_page["records"][0]["field_key"] != field["field_key"]
    full = agent_company.query(
        root,
        row,
        dataset=None,
        query="",
        period=None,
        mode="facts",
        limit=20,
        field_keys=[field["field_key"]],
    )
    assert len(full["records"]) == 12 and not full["has_more"]


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_query_error_duplicate_correction_calculation_and_citable_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime: str,
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    metric_archive(root)
    calls = 0
    input_id = ""
    code = ""
    refs = []

    def decide(_root, messages, _model):
        nonlocal calls, input_id, code, refs
        calls += 1
        text = "\n".join(m["content"] for m in messages)
        if calls == 1:
            assert "跨授权数据集" in messages[0]["content"]
            return decision(
                "query_company_data", {"mode": "fields", "query": "ROE", "period_kind": "annual"}
            )
        if calls == 2:
            field = next(
                r["field_key"]
                for r in last_result(messages)["records"]
                if r["field_key"].endswith(":data")
            )
            return decision(
                "run_readonly_sql",
                {
                    "sql": f"SELECT fact_id,period,value_decimal,unit FROM financial_facts WHERE field_key='{field}' AND period_kind='annual' ORDER BY period DESC LIMIT 4"
                },
            )
        if calls == 3:
            data = last_result(messages)
            input_id = data["analysis_id"]
            refs = [
                {k: r[k] for k in ("source_id", "locator", "relation")}
                for r in data["citable_refs"]
            ]
            code = "def analyze(rows):\n    return {'value': rows[0].get('value_decimal')}"
            return decision("run_analysis_function", {"analysis_id": input_id, "code": code})
        if calls == 4:
            error = last_result(messages)
            assert error["code"] == "analysis_function_not_allowed" and error["executed"] is False
            assert input_id in text and "available_input" in text and "last_error" in text
            return decision("run_analysis_function", {"analysis_id": input_id, "code": code})
        if calls == 5:
            assert last_result(messages)["code"] == "repeated_tool_call"
            return decision(
                "run_analysis_function",
                {
                    "analysis_id": input_id,
                    "code": "def analyze(rows):\n    ordered = sorted(rows, key=lambda r: r['period'])\n    return {'samples': [{'period': r['period'], 'value': float(r['value_decimal'])} for r in ordered], 'mean': sum([float(r['value_decimal']) for r in rows]) / len(rows)}",
                },
            )
        assert calls == 6
        assert last_result(messages)["result"]["mean"] == 25
        assert "calculation" in text and "available_input" in text
        return {
            "type": "final",
            "answer": "计算结果如下，原始单位未确认。",
            "claims": [{"text": "使用归档原始数值。", "evidence": refs}],
            "numeric_blocks": [{"analysis_id": input_id, "operations": ["mean"]}],
            "gaps": ["单位未知，无法表述为百分点"],
        }, 10

    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(
            question="分析2022–2025年 ROE 原始值平均", context="company_latest", allow_network=False
        ),
        model_client=agent_model.FunctionAgentModelClient(decide),
        tool_names=["query_company_data", "run_readonly_sql", "run_analysis_function"],
    )
    assert result["status"] == "completed" and calls == 6
    status = agent.get_status(root, result["session_id"])
    record = status["turns"][0]["working_record"]
    assert (
        record["available_input"]["analysis_id"] == input_id
        and record["calculation"]["result"]["mean"] == 25
    )
    assert "last_error" not in record
    assert record["pending"] == []
    with storage.open_db(root) as connection:
        analyses = storage.list_analysis_runs(
            connection, result["session_id"], turn_id=result["turn_id"]
        )
        events = storage.list_agent_events(connection, result["session_id"])
    assert len(analyses) == 1 and analyses[0]["status"] == "function_succeeded"
    assert len([e for e in events if e["event_type"] == "tool_error"]) == 1
    assert len([e for e in events if e["event_type"] == "tool_blocked"]) == 1


def test_working_record_survives_old_interactions_compression_and_other_turns(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    waiting = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="四年数据"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "ask_user", "question": "期间？"}, 1)]
        ),
    )
    tid = waiting["turn_id"]
    events = [
        {
            "turn_id": tid,
            "event_type": "tool_result",
            "payload_json": json.dumps(
                {
                    "tool": "run_readonly_sql",
                    "result": {
                        "analysis_id": "kept-input",
                        "rows": [{"period": "2024/FY", "value_decimal": "10", "unit": None}],
                    },
                }
            ),
        },
        *[
            {
                "turn_id": tid,
                "event_type": "tool_result",
                "payload_json": json.dumps({"tool": "list_sources", "result": {}}),
            }
            for _ in range(8)
        ],
        {
            "turn_id": tid,
            "event_type": "tool_error",
            "payload_json": json.dumps(
                {
                    "tool": "run_analysis_function",
                    "code": "analysis_function_not_allowed",
                    "analysis_id": "kept-input",
                    "node": "Attribute",
                    "line": 2,
                }
            ),
        },
        {
            "turn_id": "other-turn",
            "event_type": "tool_result",
            "payload_json": json.dumps(
                {"tool": "run_readonly_sql", "result": {"analysis_id": "forbidden-other"}}
            ),
        },
    ]
    record = agent_working_record.build(events, tid, "四年数据")
    assert record["available_input"]["analysis_id"] == "kept-input"
    assert record["last_error"]["node"] == "Attribute"
    history = [
        agent_context.ContextMessage("user", "四年数据"),
        agent_context.ContextMessage(
            "tool_error", {"code": "analysis_function_not_allowed"}, "run_analysis_function"
        ),
    ]
    messages = agent_context.convert_to_llm(
        "规则", agent_context.transform_context(history, record)
    )
    messages.insert(1, {"role": "user", "content": "旧交互" * 10000})
    compressed = agent_memory.compress_messages(
        root,
        waiting["session_id"],
        "四年数据",
        messages,
        max_input_tokens=1600,
        working_record=record,
    )
    text = "\n".join(m["content"] for m in compressed)
    assert "kept-input" in text and "Attribute" in text and "forbidden-other" not in text
    assert agent_memory.estimate_input_tokens(compressed) <= 1600


def test_recovery_allowance_and_final_only_survive_restart(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    row = turn_row(root)
    _, _, attempt_id = agent._start_attempt(
        root, row["id"], row["turn_id"], kind="initial_question", input_payload={}
    )

    def state():
        value = agent_execution.ToolExecutionState(
            root, row, row["turn_id"], attempt_id, ("run_analysis_function",), None, "计算"
        )
        agent_execution.restore_tool_state(value)
        return value

    first = state()
    arguments = {"analysis_id": "syntax-only-input", "code": "invalid"}

    def fail(*args):
        raise query_workspace.QueryWorkspaceError("analysis_function_not_allowed", "不支持语法")

    outcome = agent_execution.execute_tool(
        first, agent_execution.prepare_tool(first, "run_analysis_function", arguments), 1, fail
    )
    assert outcome.error and not outcome.error.executed
    blocked = agent_execution.prepare_tool(first, "run_analysis_function", arguments)
    assert blocked.blocked and blocked.blocked.error.code == "repeated_tool_call"
    restored = state()
    assert restored.correction_used and restored.failures == 1
    again = agent_execution.prepare_tool(restored, "run_analysis_function", arguments)
    assert again.blocked and again.blocked.error.code == "tool_recovery_exhausted"
    final_state = state()
    assert final_state.final_only
    with pytest.raises(AgentError, match="无工具收尾"):
        agent_execution.prepare_tool(final_state, "run_analysis_function", {"code": "different"})


def test_successful_unrelated_call_does_not_reset_error_allowance(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    row = turn_row(root)
    _, _, attempt_id = agent._start_attempt(
        root, row["id"], row["turn_id"], kind="initial_question", input_payload={}
    )
    state = agent_execution.ToolExecutionState(
        root, row, row["turn_id"], attempt_id, ("run_analysis_function", "get_status"), None, "计算"
    )

    def fail(*args):
        raise query_workspace.QueryWorkspaceError("analysis_function_not_allowed", "不支持语法")

    for index in range(3):
        call = agent_execution.prepare_tool(state, "run_analysis_function", {"code": str(index)})
        agent_execution.execute_tool(state, call, index * 2 + 1, fail)
        if index < 2:
            call = agent_execution.prepare_tool(state, "get_status", {"page": index})
            agent_execution.execute_tool(state, call, index * 2 + 2, lambda *args: {"status": "ok"})
    assert state.failures == 3 and state.final_only


def test_first_request_after_restart_includes_restored_input_and_error() -> None:
    history = [agent_context.ContextMessage("user", "继续计算")]
    record = {
        "available_input": {"analysis_id": "saved-input", "row_count": 4},
        "last_error": {"code": "analysis_function_not_allowed", "executed": False},
    }
    projected = agent_context.convert_to_llm(
        "规则", agent_context.transform_context(history, record)
    )
    text = json.dumps(projected, ensure_ascii=False)
    assert "saved-input" in text and "analysis_function_not_allowed" in text
    assert len(history) == 1


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_repeated_failure_produces_honest_final_within_budget(
    tmp_path: Path, monkeypatch, runtime: str
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    request = decision(
        "run_analysis_function",
        {
            "analysis_id": "00000000-0000-4000-8000-000000000099",
            "code": "def analyze(rows):\n    import os\n    return {}",
        },
    )
    client = agent_model.ScriptedAgentModelClient(
        [
            request,
            request,
            request,
            (
                {
                    "type": "final",
                    "answer": "计算未完成，不能推断数值。",
                    "gaps": ["受限语法未纠正"],
                },
                10,
            ),
        ]
    )
    result = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="计算", token_budget=256),
        model_client=client,
        tool_names=["run_analysis_function"],
    )
    assert result["status"] == "completed" and result["report"]["gaps"]
    status = agent.get_status(root, result["session_id"])
    assert status["turns"][0]["used_tokens"] == 40
    assert status["turns"][0]["working_record"]["final_only"] is True


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_chat_failure_is_linked_before_model_and_retry_keeps_same_turn(
    tmp_path: Path, monkeypatch, runtime: str
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    plan = RoutePlan(
        intent="interactive_question",
        ticker="SYNTHETIC",
        question="临时问题",
        confidence=1,
        reason="测试",
        requires_confirmation=False,
    )
    monkeypatch.setattr(chat.router, "route", lambda *a, **kw: plan)
    observed = []

    def fail_model(_root, messages, _model):
        with storage.open_db(root) as connection:
            linked = connection.execute("SELECT * FROM chat_sessions").fetchone()
            pending = json.loads(linked["pending_json"])
            observed.append((linked["last_agent_session_id"], pending["turn_id"]))
        raise AgentError("model_request_failed", "测试网络失败", consumed_tokens=3)

    monkeypatch.setattr(agent, "_api_call_for_root", fail_model)
    failed = chat.run_once(root, "临时问题")
    assert failed["status"] == "failed"
    assert observed == [(failed["agent_session_id"], failed["turn_id"])]
    monkeypatch.setattr(
        agent,
        "_api_call_for_root",
        lambda *a, **kw: ({"type": "final", "answer": "已恢复", "gaps": []}, 2),
    )
    recovered = chat.run_once(root, "重试", session_id=failed["session_id"])
    assert recovered["result"]["turn_id"] == failed["turn_id"]
    status = agent.get_status(root, failed["agent_session_id"])
    assert len(status["turns"]) == 1 and status["turns"][0]["used_tokens"] == 5


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_invalid_tool_arguments_are_also_counted_and_bounded(
    tmp_path: Path, monkeypatch, runtime: str
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    request = decision(
        "run_analysis_function",
        {"analysis_id": "invalid-uuid", "code": "def analyze(rows):\n    return {}"},
    )
    result = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="错误参数"),
        tool_names=["run_analysis_function"],
        model_client=agent_model.ScriptedAgentModelClient(
            [
                request,
                request,
                request,
                ({"type": "final", "answer": "参数错误，计算未执行。", "gaps": ["计算未执行"]}, 10),
            ]
        ),
    )
    with storage.open_db(root) as connection:
        events = storage.list_agent_events(connection, result["session_id"])
    errors = [json.loads(e["payload_json"]) for e in events if e["event_type"] == "tool_error"]
    blocks = [e for e in events if e["event_type"] == "tool_blocked"]
    assert len(errors) == 1 and errors[0]["executed"] is False and len(blocks) == 2
    assert agent.get_status(root, result["session_id"])["turns"][0]["working_record"]["final_only"]


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_recovery_cannot_exceed_remaining_model_rounds(
    tmp_path: Path, monkeypatch, runtime: str
) -> None:
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    calls = 0

    def decide(*args):
        nonlocal calls
        calls += 1
        return decision(
            "run_analysis_function",
            {
                "analysis_id": "00000000-0000-4000-8000-000000000099",
                "code": "def analyze(rows):\n    import os\n    return {}",
            },
        )

    with pytest.raises(AgentError):
        agent.ask(
            root,
            "SYNTHETIC",
            InteractiveQuestion(question="只有两轮", max_iterations=2),
            tool_names=["run_analysis_function"],
            model_client=agent_model.FunctionAgentModelClient(decide),
        )
    assert calls == 2
    with storage.open_db(root) as connection:
        sid = connection.execute("SELECT id FROM agent_sessions").fetchone()[0]
        events = storage.list_agent_events(connection, sid)
    assert len([e for e in events if e["event_type"] == "tool_error"]) == 1
    assert not [e for e in events if e["event_type"] == "tool_blocked"]


def test_empty_query_is_not_recorded_as_obtained_data() -> None:
    record = agent_working_record.build(
        [
            {
                "turn_id": "turn",
                "event_type": "tool_result",
                "payload_json": json.dumps(
                    {
                        "tool": "run_readonly_sql",
                        "result": {"analysis_id": "empty", "row_count": 0, "rows": []},
                    }
                ),
            }
        ],
        "turn",
        "查数据",
    )
    assert record["empty_query"]["analysis_id"] == "empty"
    assert "available_input" not in record and not record["completed"]
