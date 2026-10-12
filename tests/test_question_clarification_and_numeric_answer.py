"""五题的共性回归：虚构公司、变动指标、离线脚本模型；不调用真实 API。"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_agent_recovery_working_record import decision, last_result
from tests.test_company_agent import turn_row
from tests.test_company_data import register, statements
from tests.test_storage import make_root
from wonder_stockresearch import agent, chat, skill_registry, storage
from wonder_stockresearch.interactive import (
    agent_company,
    agent_model,
    agent_policy,
    numeric_answer,
    query_workspace,
    question_requirements,
)
from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import (
    AgentDecision,
    InteractiveFollowup,
    InteractiveQuestion,
    NumericBlockRequest,
)


def archive(
    root: Path,
    label: str = "虚构指标",
    values: tuple = ("17", "11", "14", "9", "8"),
    *,
    unit: str | None = None,
):
    payload = statements()
    statement = payload["statements"]["1"]
    statement["structure_list"][0]["display_name"] = label
    original = statement["report_list"][0]
    statement["report_list"] = []
    for year, value in zip(range(2021, 2021 + len(values)), values, strict=True):
        row = copy.deepcopy(original)
        row.update(period_text=f"{year}/FY", fiscal_year=year, date_time_str=f"{year}-12-31")
        row["item_list"] = [{"field_id": 9001, "data": value, "unit": unit}]
        statement["report_list"].append(row)
    return register(root, "statements", payload)


def sql(
    years="2021/FY','2022/FY','2023/FY','2024/FY','2025/FY", *, sort="CAST(value_decimal AS REAL)"
):
    return f"SELECT fact_id, period, value_decimal FROM financial_facts WHERE field_key LIKE '%:data' AND period IN ('{years}') ORDER BY {sort}"


def model_for_sql(query: str, operations: list[str], *, bad_first: str | None = None):
    count = 0
    analysis_id = None

    def call(_root, messages, _model):
        nonlocal count, analysis_id
        count += 1
        if count == 1:
            return decision("run_readonly_sql", {"sql": query})
        if analysis_id is None:
            analysis_id = last_result(messages)["analysis_id"]
        return {
            "type": "final",
            "answer": bad_first
            if count == 2 and bad_first
            else "可核验的结果如下；归档不足以说明经营原因。",
            "numeric_blocks": [{"analysis_id": analysis_id, "operations": operations}],
        }, 10

    return agent_model.FunctionAgentModelClient(call)


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_relative_question_waits_before_model_and_confirmation_resumes_same_turn(
    tmp_path: Path, monkeypatch, runtime
):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    monkeypatch.setattr(storage, "now_utc", lambda: datetime(2026, 10, 11, tzinfo=UTC))
    root = make_root(tmp_path)
    archive(root, "净资产收益率")
    waiting = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(
            question="近三年的ROE是什么值", context="company_latest", allow_network=False
        ),
        model_client=agent_model.ScriptedAgentModelClient([]),
    )
    assert waiting["status"] == "waiting_user" and "2023、2024、2025" in waiting["question"]
    assert agent.get_status(root, waiting["session_id"])["turns"][0]["used_tokens"] == 0
    result = agent.respond(
        root,
        waiting["session_id"],
        waiting["turn_id"],
        InteractiveFollowup(question="是"),
        model_client=model_for_sql(sql("2023/FY','2024/FY','2025/FY"), ["values", "changes"]),
    )
    assert result["turn_id"] == waiting["turn_id"]
    assert result["report"]["task_requirements"]["target_years"] == [2023, 2024, 2025]
    assert "2022/FY" not in result["report"]["answer"]
    assert agent.get_status(root, waiting["session_id"])["turns"][0]["used_tokens"] == 20
    assert len(agent.get_status(root, waiting["session_id"])["turns"][0]["attempts"]) == 2


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
@pytest.mark.parametrize("label", ["毛利率", "自定义效率指标"])
def test_numeric_identity_and_statistics_survive_value_sorting(
    tmp_path: Path, monkeypatch, runtime, label
):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    archive(root, label)
    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(
            question="列出2021–2025年数值，算均值、极差和总体标准差",
            context="company_latest",
            allow_network=False,
        ),
        model_client=model_for_sql(
            sql(), ["values", "mean", "range", "population_stddev", "changes"]
        ),
    )
    block = result["report"]["numeric_blocks"][0]
    assert [(r["period"], r["value_decimal"]) for r in block["rows"]] == [
        (f"{y}/FY", v) for y, v in zip(range(2021, 2026), ("17", "11", "14", "9", "8"), strict=True)
    ]
    assert Decimal(block["statistics"]["mean"]) == Decimal("11.8")
    assert Decimal(block["statistics"]["range"]) == 9
    assert abs(
        Decimal(block["statistics"]["population_stddev"]) - Decimal("3.31058907144937")
    ) < Decimal("0.000000000001")
    assert block["changes"][-1]["direction"] == "下降"
    assert "%" not in result["report"]["answer"] and "百分点" not in result["report"]["answer"]
    assert "单位未确认" in result["report"]["answer"]
    assert len(result["report"]["evidence_refs"]) == 5


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_three_year_mean_uses_target_rows_and_does_not_require_model_code(
    tmp_path: Path, monkeypatch, runtime
):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    archive(root, "毛利率")
    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="2023–2025年毛利率均值", context="company_latest"),
        model_client=model_for_sql(sql("2023/FY','2024/FY','2025/FY"), ["mean"]),
    )
    assert abs(
        Decimal(result["report"]["numeric_blocks"][0]["statistics"]["mean"]) - Decimal(31) / 3
    ) < Decimal("1e-26")
    with storage.open_db(root) as connection:
        names = [
            json.loads(e["payload_json"]).get("tool")
            for e in storage.list_agent_events(connection, result["session_id"])
            if e["event_type"] == "tool_result"
        ]
    assert names == ["run_readonly_sql"]


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_unsupported_code_can_be_replaced_by_generic_numeric_submission(
    tmp_path: Path, monkeypatch, runtime
):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    archive(root)
    step, aid = 0, None

    def call(_root, messages, _model):
        nonlocal step, aid
        step += 1
        if step == 1:
            return decision("run_readonly_sql", {"sql": sql()})
        if step == 2:
            aid = last_result(messages)["analysis_id"]
            return decision(
                "run_analysis_function",
                {
                    "analysis_id": aid,
                    "code": "def analyze(rows):\n    return {'mean': statistics.mean(rows)}",
                },
            )
        error = last_result(messages)
        assert "mean" in error["message"] and error["executed"] is False
        return {
            "type": "final",
            "answer": "计算结果如下。",
            "numeric_blocks": [{"analysis_id": aid, "operations": ["mean"]}],
        }, 10

    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="2021–2025年均值", context="company_latest"),
        model_client=agent_model.FunctionAgentModelClient(call),
    )
    assert result["status"] == "completed" and "11.8" in result["report"]["answer"]


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_wrong_numeric_narrative_gets_one_repair_without_requery(
    tmp_path: Path, monkeypatch, runtime
):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    archive(root)
    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="2021–2025年变化", context="company_latest"),
        model_client=model_for_sql(
            sql(), ["values", "changes"], bad_first="连续两年回升，均值11.8%。"
        ),
    )
    assert result["status"] == "completed"
    with storage.open_db(root) as connection:
        events = storage.list_agent_events(connection, result["session_id"])
    assert len([e for e in events if e["event_type"] == "answer_repair"]) == 1
    assert len([e for e in events if e["event_type"] == "tool_result"]) == 1


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_missing_target_year_asks_and_does_not_substitute_old_year(
    tmp_path: Path, monkeypatch, runtime
):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    archive(root, values=("17", "11", "14", "9"))
    waiting = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="2023–2025年均值", context="company_latest"),
        model_client=model_for_sql(sql("2023/FY','2024/FY','2025/FY"), ["mean"]),
    )
    assert waiting["status"] == "waiting_user" and "2025" in waiting["question"]
    with storage.open_db(root) as connection:
        aid = storage.list_analysis_runs(
            connection, waiting["session_id"], turn_id=waiting["turn_id"]
        )[0]["id"]
    result = agent.respond(
        root,
        waiting["session_id"],
        waiting["turn_id"],
        InteractiveFollowup(question="保留目标，列出缺口"),
        model_client=agent_model.ScriptedAgentModelClient(
            [
                (
                    {
                        "type": "final",
                        "answer": "当前覆盖的计算如下。",
                        "gaps": ["目标中的最新年度未取得"],
                        "numeric_blocks": [{"analysis_id": aid, "operations": ["mean"]}],
                    },
                    10,
                )
            ]
        ),
    )
    assert result["report"]["task_requirements"]["target_years"] == [2023, 2024, 2025]
    assert "2022/FY" not in result["report"]["answer"]


def test_aliases_prioritize_actual_candidate_labels_without_merging_accounts(tmp_path: Path):
    root = make_root(tmp_path)
    payload = statements(100)
    statement = payload["statements"]["1"]
    statement["structure_list"] = [
        {"field_id": n, "display_name": label}
        for n, label in enumerate(["资产合计", "负债合计", "现金及等价物", "总资产周转率"], 1)
    ]
    statement["report_list"][0]["item_list"] = [
        {"field_id": n, "data": n * 10} for n in range(1, 5)
    ]
    register(root, "statements", payload)
    row = turn_row(root)
    for term, expected in [
        ("总资产", "资产合计"),
        ("total liabilities", "负债合计"),
        ("现金资产", "现金及等价物"),
    ]:
        result = agent_company.query(
            root, row, dataset=None, query=term, period=None, mode="fields", limit=10
        )
        assert result["records"][0]["label"] == expected
        assert "locator" not in result["records"][0]
    assert agent_company.search_terms("总资产") == agent_company.search_terms("资产合计")


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_quarter_gap_stops_equivalent_sql_but_preserves_honest_scope(
    tmp_path: Path, monkeypatch, runtime
):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    archive(root)
    steps = 0

    def call(_root, messages, _model):
        nonlocal steps
        steps += 1
        if steps == 1:
            return decision(
                "run_readonly_sql",
                {"sql": "SELECT fact_id FROM financial_facts WHERE period_kind='quarter'"},
            )
        if steps == 2:
            result = last_result(messages)
            assert result["coverage_diagnostic"]["period_kind_unavailable"]
            return decision(
                "run_readonly_sql",
                {
                    "sql": "SELECT fact_id,period FROM financial_facts WHERE period_kind = 'quarter' ORDER BY period"
                },
            )
        assert last_result(messages)["code"] == "known_coverage_gap"
        return {
            "type": "final",
            "answer": "本次授权归档只取得年度事实，季度数据尚未取得。",
            "gaps": ["季度覆盖缺口"],
        }, 10

    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(
            question="2025年单季度收入", context="company_latest", allow_network=False
        ),
        model_client=agent_model.FunctionAgentModelClient(call),
    )
    with storage.open_db(root) as connection:
        analyses = storage.list_analysis_runs(
            connection, result["session_id"], turn_id=result["turn_id"]
        )
    assert len(analyses) == 1 and result["status"] == "completed"


@pytest.mark.parametrize(
    "date, expected", [("2026-01-01", [2023, 2024, 2025]), ("2027-01-01", [2024, 2025, 2026])]
)
def test_clock_changes_candidates_but_unknown_fiscal_calendar_still_asks(date, expected):
    clock = {"date": date, "as_of": date, "timezone": "Asia/Shanghai"}
    task = question_requirements.resolve("过去3个财年ROE", clock)
    # 财年词不凭自然年日历确认；简单解析无法覆盖时 Skill 仍应澄清。
    assert task.get("basis") is None
    task = question_requirements.resolve("近3年ROE", clock)
    assert task["target_years"] == expected and task["unresolved"]


def test_explicit_years_do_not_ask_and_skill_is_small_and_unprivileged(tmp_path: Path):
    root = make_root(tmp_path)
    method = question_requirements.load_method(root)
    audit = skill_registry.selection_audit(method)
    assert audit[0]["name"] == "question-clarification" and audit[0]["tools"] == []
    assert len(method[0].instructions) < 2000
    task = question_requirements.resolve("2023至2025年均值", {"date": "2026-10-11"})
    assert task["target_years"] == [2023, 2024, 2025] and not task["unresolved"]
    assert "此前获准的计算能力仍存在" in agent_policy.build_system_prompt(
        "general", [], final_only=True
    )


def test_model_cannot_submit_foreign_or_fabricated_input(tmp_path: Path):
    root = make_root(tmp_path)
    archive(root)
    row = turn_row(root)
    result = query_workspace.run_readonly_sql(root, row, row["turn_id"], sql())
    query_workspace.record_tool_evidence_receipts(
        root, row, row["turn_id"], "run_readonly_sql", result
    )
    with pytest.raises(AgentError, match="未出现在"):
        numeric_answer.prepare(
            root,
            row,
            row["turn_id"],
            AgentDecision(
                type="final",
                answer="结果如下。",
                numeric_blocks=[
                    NumericBlockRequest(analysis_id=result["analysis_id"], fact_ids=["fabricated"])
                ],
            ),
            {},
        )
    other = turn_row(root)
    with pytest.raises(AgentError, match="不属于"):
        numeric_answer.prepare(
            root,
            other,
            other["turn_id"],
            AgentDecision(
                type="final",
                answer="结果如下。",
                numeric_blocks=[NumericBlockRequest(analysis_id=result["analysis_id"])],
            ),
            {},
        )


def test_claimed_sql_values_do_not_override_canonical_input(tmp_path: Path):
    root = make_root(tmp_path)
    archive(root)
    row = turn_row(root)
    result = query_workspace.run_readonly_sql(
        root,
        row,
        row["turn_id"],
        "SELECT fact_id, '2099/FY' AS period, '999' AS value_decimal FROM financial_facts WHERE field_key LIKE '%:data'",
    )
    assert {r["period"] for r in result["rows"]} == {"2099/FY"}
    assert {r["period"] for r in result["verified_facts"]} == {f"{y}/FY" for y in range(2021, 2026)}
    assert "999" not in {r["value_decimal"] for r in result["verified_facts"]}


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_semantic_reply_requires_visible_candidate_confirmation(tmp_path, monkeypatch, runtime):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    archive(root)
    waiting = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="近三年数值", context="company_latest"),
        model_client=agent_model.ScriptedAgentModelClient([]),
    )
    proposed = agent.respond(
        root,
        waiting["session_id"],
        waiting["turn_id"],
        InteractiveFollowup(question="按公司财年"),
        model_client=agent_model.ScriptedAgentModelClient(
            [
                (
                    {
                        "type": "ask_user",
                        "question": "按2022–2024这些公司财年可以吗？",
                        "task": {"periods": ["2022/FY", "2023/FY", "2024/FY"], "unresolved": []},
                    },
                    10,
                )
            ]
        ),
    )
    assert proposed["status"] == "waiting_user"
    result = agent.respond(
        root,
        waiting["session_id"],
        waiting["turn_id"],
        InteractiveFollowup(question="是"),
        model_client=model_for_sql(sql("2022/FY','2023/FY','2024/FY"), ["values"]),
    )
    assert result["report"]["task_requirements"]["target_years"] == [2022, 2023, 2024]
    assert agent.get_status(root, waiting["session_id"])["turns"][0]["used_tokens"] == 30


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_chat_waiting_response_and_followup_keep_correct_turn(tmp_path, monkeypatch, runtime):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    archive(root)
    first = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(question="近三年数值", context="company_latest"),
        model_client=agent_model.ScriptedAgentModelClient([]),
    )
    chat_id = chat._new_session(root, "近三年数值")
    saved = chat._store_interactive_result(root, chat_id, first)
    assert saved["status"] == "waiting_user"
    original_respond, original_followup = agent.respond, agent.followup
    client = model_for_sql(sql("2023/FY','2024/FY','2025/FY"), ["values"])
    monkeypatch.setattr(
        agent, "respond", lambda *a, **kw: original_respond(*a, **kw, model_client=client)
    )
    monkeypatch.setattr(chat.router, "route", lambda *a, **kw: pytest.fail("恢复不得经过Router"))
    result = chat.run_once(root, "是", session_id=chat_id)
    assert result["result"]["turn_id"] == first["turn_id"]
    assert chat._pending(chat._row(root, chat_id)) is None
    monkeypatch.setattr(
        agent,
        "followup",
        lambda *a, **kw: original_followup(
            *a,
            **kw,
            model_client=agent_model.ScriptedAgentModelClient(
                [
                    (
                        {
                            "type": "final",
                            "answer": "尚未取得经营原因证据。",
                            "gaps": ["原因缺口"],
                        },
                        10,
                    )
                ]
            ),
        ),
    )
    second = chat.run_once(root, "解释原因", session_id=chat_id)
    assert second["result"]["turn_id"] != first["turn_id"]
    assert second["result"]["session_id"] == first["session_id"]


def test_unknown_units_from_different_sources_do_not_form_one_series():
    base = {
        "fact_id": "a",
        "field_key": "any:data",
        "label": "任意指标",
        "period": "2024/FY",
        "value_decimal": "1",
        "source_id": "source-a",
        "unit": None,
        "scale": None,
    }
    other = {
        **base,
        "fact_id": "b",
        "source_id": "source-b",
        "period": "2025/FY",
        "value_decimal": "10",
    }
    blocks = numeric_answer.calculate([base, other], ["mean"])
    assert len(blocks) == 2
    assert {b["statistics"]["mean"] for b in blocks} == {"1", "10"}
    confirmed = [{**r, "unit": "%", "scale": "1"} for r in (base, other)]
    blocks = numeric_answer.calculate(confirmed, ["mean", "changes"])
    assert len(blocks) == 1 and blocks[0]["statistics"]["mean"] == "5.5"
    assert "百分点" in numeric_answer.render(blocks)


@pytest.mark.parametrize(
    "mismatch, code",
    [
        ({"value_attribute": "yoy"}, "numeric_attribute_mismatch"),
        ({"period_kind": "quarter"}, "numeric_period_kind_mismatch"),
        ({"required_operations": ["mean"]}, "numeric_operation_missing"),
        ({"target_years": [2024, 2025]}, "numeric_period_mismatch"),
    ],
)
def test_numeric_contract_rejects_wrong_attribute_kind_operation_and_range(
    tmp_path, mismatch, code
):
    root = make_root(tmp_path)
    archive(root)
    row = turn_row(root)
    result = query_workspace.run_readonly_sql(root, row, row["turn_id"], sql())
    query_workspace.record_tool_evidence_receipts(
        root, row, row["turn_id"], "run_readonly_sql", result
    )
    with pytest.raises(AgentError) as captured:
        numeric_answer.prepare(
            root,
            row,
            row["turn_id"],
            AgentDecision(
                type="final",
                answer="结果如下。",
                numeric_blocks=[NumericBlockRequest(analysis_id=result["analysis_id"])],
            ),
            mismatch,
        )
    assert captured.value.code == code


def test_confirmed_calendar_rule_does_not_override_fiscal_year_request():
    task = question_requirements.resolve(
        "近3个财年", {"date": "2026-10-11"}, confirmed={"basis": "calendar_year"}
    )
    assert task["unresolved"] == ["relative_period_basis"]


def test_non_december_fiscal_year_cannot_replace_confirmed_calendar_year(tmp_path):
    root = make_root(tmp_path)
    payload = statements(100)
    register(root, "statements", payload)
    row = turn_row(root)
    result = query_workspace.run_readonly_sql(
        root,
        row,
        row["turn_id"],
        "SELECT fact_id FROM financial_facts WHERE field_key LIKE '%:data'",
    )
    query_workspace.record_tool_evidence_receipts(
        root, row, row["turn_id"], "run_readonly_sql", result
    )
    with pytest.raises(AgentError, match="12月31日"):
        numeric_answer.prepare(
            root,
            row,
            row["turn_id"],
            AgentDecision(
                type="final",
                answer="结果如下。",
                numeric_blocks=[NumericBlockRequest(analysis_id=result["analysis_id"])],
            ),
            {"basis": "calendar_year", "period_kind": "annual"},
        )


def test_bare_financial_numbers_cannot_bypass_numeric_submission(tmp_path):
    root = make_root(tmp_path)
    archive(root)
    row = turn_row(root)
    query_workspace.run_readonly_sql(root, row, row["turn_id"], sql())
    with pytest.raises(AgentError) as captured:
        numeric_answer.prepare(
            root,
            row,
            row["turn_id"],
            AgentDecision(
                type="final",
                answer="总资产为12345。",
            ),
            {"target_years": [2025]},
        )
    assert captured.value.code == "numeric_block_required"


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_repeated_invalid_answer_has_only_one_repair_across_retry(tmp_path, monkeypatch, runtime):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    archive(root)
    client = model_for_sql(sql(), ["mean"], bad_first="均值11.8%。")
    # 第二次也拒绝纠正，不能获得无限修复或新预算。
    original = client.decide

    def bad(root, messages, model):
        payload, consumed = original(root, messages, model)
        if payload.get("type") == "final":
            payload["answer"] = "均值11.8%。"
        return payload, consumed

    with pytest.raises(AgentError) as error:
        agent.ask(
            root,
            "US.MU",
            InteractiveQuestion(
                question="2021–2025年均值",
                context="company_latest",
            ),
            model_client=agent_model.FunctionAgentModelClient(bad),
        )
    assert error.value.code == "numeric_text_not_allowed"
    with storage.open_db(root) as connection:
        session = connection.execute("SELECT * FROM agent_sessions").fetchone()
        turn = storage.list_agent_turns(connection, session["id"])[0]
        events = storage.list_agent_events(connection, session["id"])
        aid = storage.list_analysis_runs(connection, session["id"], turn_id=turn["id"])[0]["id"]
    assert len([e for e in events if e["event_type"] == "answer_repair"]) == 1
    before = turn["used_tokens"]
    with pytest.raises(AgentError):
        agent.retry(
            root,
            session["id"],
            turn["id"],
            model_client=agent_model.ScriptedAgentModelClient(
                [
                    (
                        {
                            "type": "final",
                            "answer": "均值11.8%。",
                            "numeric_blocks": [{"analysis_id": aid, "operations": ["mean"]}],
                        },
                        10,
                    )
                ]
            ),
        )
    with storage.open_db(root) as connection:
        updated = storage.get_agent_turn(connection, turn["id"])
        events = storage.list_agent_events(connection, session["id"])
    assert updated["used_tokens"] == before + 10
    assert len([e for e in events if e["event_type"] == "answer_repair"]) == 1


def test_skill_change_is_detected_when_resuming_frozen_turn(tmp_path):
    root = make_root(tmp_path)
    archive(root)
    skill_dir = root / "skills" / "question-clarification"
    source = Path(__file__).resolve().parents[1] / "skills" / "question-clarification"
    shutil.copytree(source, skill_dir)
    waiting = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(
            question="近三年数值",
            context="company_latest",
        ),
        model_client=agent_model.ScriptedAgentModelClient([]),
    )
    path = skill_dir / "SKILL.md"
    path.write_text(path.read_text() + "\n方法变更。\n", encoding="utf-8")
    with pytest.raises(AgentError) as error:
        agent.respond(
            root,
            waiting["session_id"],
            waiting["turn_id"],
            InteractiveFollowup(question="是"),
            model_client=agent_model.ScriptedAgentModelClient([]),
        )
    assert error.value.code == "skill_changed_since_selection"


def test_explicit_quarter_and_changed_years_keep_quarter_requirements():
    task = question_requirements.resolve("2024Q1收入", {"date": "2026-10-11"})
    assert task["period_kind"] == "quarter" and task["target_periods"] == ["2024/Q1"]


def test_changing_years_of_all_quarters_rebuilds_target_quarters(tmp_path):
    root = make_root(tmp_path)
    archive(root)
    waiting = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(
            question="近两年四个单季度收入",
            context="company_latest",
        ),
        model_client=agent_model.ScriptedAgentModelClient([]),
    )
    agent.respond(
        root,
        waiting["session_id"],
        waiting["turn_id"],
        InteractiveFollowup(question="改为2022、2023年"),
        model_client=agent_model.ScriptedAgentModelClient(
            [({"type": "ask_user", "question": "是否补充资料？"}, 10)]
        ),
    )
    with storage.open_db(root) as connection:
        row = storage.get_agent_session(connection, waiting["session_id"])
    task = question_requirements.load(root, row, waiting["turn_id"])
    assert task["target_years"] == [2022, 2023]
    assert task["target_periods"] == [f"{y}/Q{q}" for y in (2022, 2023) for q in range(1, 5)]


@pytest.mark.parametrize("runtime", ["legacy", "pi"])
def test_balance_sheet_alias_discovery_reaches_real_values_with_receipts(
    tmp_path, monkeypatch, runtime
):
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", runtime)
    root = make_root(tmp_path)
    payload = statements(100)
    statement = payload["statements"]["1"]
    statement["structure_list"] = [
        {"field_id": n, "display_name": label}
        for n, label in enumerate(["资产合计", "负债合计", "现金及等价物"], 1)
    ]
    statement["report_list"][0]["item_list"] = [
        {"field_id": n, "data": n * 10} for n in range(1, 4)
    ]
    register(root, "statements", payload)
    step = 0

    def call(_root, messages, _model):
        nonlocal step
        step += 1
        if step == 1:
            return decision(
                "query_company_data",
                {"mode": "fields", "query": "总资产 总负债 现金", "period_kind": "annual"},
            )
        previous = last_result(messages)
        if step == 2:
            assert {r["label"] for r in previous["records"]} == {
                "资产合计",
                "负债合计",
                "现金及等价物",
            }
            keys = "','".join(r["field_key"] for r in previous["records"])
            return decision(
                "run_readonly_sql",
                {
                    "sql": f"SELECT fact_id FROM financial_facts WHERE field_key IN ('{keys}') AND period='2026/FY'"
                },
            )
        return {
            "type": "final",
            "answer": "所选财报科目如下，原始单位仍需核实。",
            "numeric_blocks": [{"analysis_id": previous["analysis_id"], "operations": ["values"]}],
        }, 10

    result = agent.ask(
        root,
        "US.MU",
        InteractiveQuestion(
            question="2026财年末总资产、总负债、现金及现金等价物分别多少，标注币种单位",
            context="company_latest",
        ),
        model_client=agent_model.FunctionAgentModelClient(call),
    )
    blocks = result["report"]["numeric_blocks"]
    assert {b["identity"]["label"]: b["rows"][0]["value_decimal"] for b in blocks} == {
        "资产合计": "10",
        "负债合计": "20",
        "现金及等价物": "30",
    }
    assert len(result["report"]["evidence_refs"]) == 3 and step == 3
