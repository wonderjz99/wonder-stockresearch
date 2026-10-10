from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from wonder_stockresearch.interactive import agent_context, agent_memory, agent_results
from wonder_stockresearch.interactive.pi import pi_context


def test_legacy_and_pi_keep_the_same_whole_financial_facts_and_page_position() -> None:
    records = [
        {
            "fact_id": f"fact-{index}",
            "label": "营业收入",
            "period": f"{2025 - index}/FY",
            "value": "123.456789",
            "unit": "元",
            "scale": "1000000",
            "currency": "CNY",
            "accounting_basis": "IFRS",
            "source_id": "source-1",
            "locator": f"$.records[{index}]",
        }
        for index in range(20)
    ]
    original = {"mode": "facts", "records": records, "offset": 5, "total_matches": 25}
    before = copy.deepcopy(original)
    native_text = pi_context.tool_projection(original)
    legacy_messages = agent_context.convert_to_llm(
        "规则",
        [
            agent_context.ContextMessage("tool_result", original, "query_archived_financials"),
        ],
    )
    legacy_text = legacy_messages[1]["content"].split("返回：", 1)[1].split("\n请继续", 1)[0]
    assert legacy_text == native_text
    view = json.loads(native_text)
    assert 0 < len(view["records"]) < len(records)
    assert view["records"] == records[: len(view["records"])]
    assert view["next_offset"] == 5 + len(view["records"])
    assert view["has_more"] is True
    assert agent_memory.estimate_input_tokens([{"content": native_text}]) <= 1500
    assert original == before


def test_tool_outcome_keeps_full_artifact_while_only_projecting_navigation() -> None:
    original = {
        "analysis_id": "analysis-1",
        "status": "function_succeeded",
        "result": {
            "function": {"explanation": "长计算说明" * 10000},
            "sql": {"rows": [{"value": 42}]},
        },
    }
    before = copy.deepcopy(original)
    outcome = agent_results.ToolOutcome(status="success", data=original)
    view = json.loads(outcome.model_text)
    assert view["analysis_id"] == "analysis-1"
    assert view["context_truncated"] is True
    assert "长计算说明" not in outcome.model_text
    assert agent_memory.estimate_input_tokens([{"content": outcome.model_text}]) <= 1500
    assert outcome.data == before
    assert original == before


def test_recoverable_failure_keeps_artifact_identity_without_becoming_empty_data() -> None:
    failure = agent_results.ToolFailure(
        code="analysis_function_failed", message="受限函数执行失败", recoverable=True
    )
    outcome = agent_results.ToolOutcome(
        status="error", data={"analysis_id": "failed-analysis"}, error=failure
    )
    view = json.loads(outcome.model_text)
    assert view["is_error"] is True
    assert view["code"] == "analysis_function_failed"
    assert view["analysis_id"] == "failed-analysis"
    assert "不能原位重跑" in view["next_step_hint"]
    assert outcome.error is not None and outcome.error.recoverable is True
    assert "model_text" not in outcome.model_dump()
    assert pi_context.recovery_hint(failure.code) == agent_results.recovery_hint(failure.code)


def test_large_failure_remains_an_error_after_model_projection() -> None:
    outcome = agent_results.ToolOutcome(
        status="error",
        error=agent_results.ToolFailure(
            code="analysis_sql_failed",
            message="执行错误说明" * 10000,
            recoverable=True,
        ),
    )
    view = json.loads(outcome.model_text)
    assert view["is_error"] is True
    assert view["code"] == "analysis_sql_failed"
    assert view["context_truncated"] is True
    assert agent_memory.estimate_input_tokens([{"content": outcome.model_text}]) <= 1500


@pytest.mark.parametrize(
    "status,error",
    [
        ("success", {"code": "tool_not_allowed", "message": "未授权"}),
        ("error", None),
    ],
)
def test_tool_outcome_rejects_ambiguous_success_and_failure(status: str, error: object) -> None:
    with pytest.raises(ValidationError):
        agent_results.ToolOutcome.model_validate({"status": status, "error": error})
