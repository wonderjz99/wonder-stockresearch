from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.test_stage18_agent import financial_run, make_root, statements_payload
from tests.test_stage19_query_workspace import _turn_row
from wonder_stockresearch import agent, futu_fundamentals, storage
from wonder_stockresearch.interactive import agent_data, analysis_workspace, query_workspace
from wonder_stockresearch.models import EvidenceRef, InteractiveQuestion, MemoRequest
from wonder_stockresearch.sources import SourceError


class Provider:
    calls = 0
    closed = 0

    def fetch(self, dataset: str, ticker: str) -> dict:
        self.calls += 1
        assert dataset == "statements" and ticker == "SYNTHETIC"
        result = copy.deepcopy(statements_payload())
        # 测试使用固定计算器并不知道的指标，不实现任何指标专用路径。
        result["payload"]["statements"]["1"]["structure_list"][0]["display_name"] = "自定义经营指标"
        return result

    def close(self) -> None:
        self.closed += 1


def tool_result(messages: list[dict[str, str]]) -> dict:
    return json.loads(messages[-1]["content"].split(" 返回：", 1)[1].split("\n请继续", 1)[0])


def test_missing_metric_fetches_then_model_writes_calculation_with_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path)
    run_id, _ = financial_run(root)
    with storage.open_db(root) as connection:
        original_sources = storage.get_run_sources(connection, run_id)
        original_run = storage.get_run(connection, run_id)
    provider = Provider()
    monkeypatch.setattr(futu_fundamentals, "make_provider", lambda: provider)
    calls = 0

    def decide(_root: Path, messages: list[dict[str, str]], _model: str) -> tuple[dict, int]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "type": "tool_call",
                "tool": "query_archived_financials",
                "arguments": {"query": "自定义经营指标"},
            }, 20
        if calls == 2:
            assert tool_result(messages)["records"] == []
            return {
                "type": "tool_call",
                "tool": "fetch_financial_data",
                "arguments": {"dataset": "statements", "query": "自定义经营指标"},
            }, 20
        if calls == 3:
            result = tool_result(messages)
            assert len(result["records"]) == 2
            return {
                "type": "tool_call",
                "tool": "run_readonly_sql",
                "arguments": {
                    "sql": "SELECT fact_id, value_decimal, period FROM financial_facts "
                    "WHERE label = '自定义经营指标' ORDER BY period DESC"
                },
            }, 20
        if calls == 4:
            return {
                "type": "tool_call",
                "tool": "run_analysis_function",
                "arguments": {
                    "analysis_id": tool_result(messages)["analysis_id"],
                    "code": "def analyze(rows):\n    total = 0\n    for row in rows:\n"
                    "        total = total + float(row['value_decimal'])\n"
                    "    return {'average': total / len(rows), 'count': len(rows)}",
                },
            }, 20
        result = tool_result(messages)
        assert result["result"] == {"average": 110.0, "count": 2}
        refs = [
            {key: ref[key] for key in ("source_id", "locator", "relation")}
            for ref in result["source_refs"]
        ]
        return {
            "type": "final",
            "answer": "两个完整财年平均为 110。",
            "claims": [{"text": "平均为 110。", "evidence": refs}],
            "evidence_refs": [],
            "counterarguments": [],
            "gaps": [],
            "suggested_next_checks": [],
        }, 20

    monkeypatch.setattr(agent, "_api_call_for_root", decide)
    result = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="自定义经营指标两年平均", run_id=run_id),
        tool_names=[
            "query_archived_financials",
            "fetch_financial_data",
            "run_readonly_sql",
            "run_analysis_function",
        ],
    )
    assert result["status"] == "completed" and provider.calls == provider.closed == 1
    sid = result["session_id"]
    with storage.open_db(root) as connection:
        assert storage.get_run(connection, run_id) == original_run
        assert storage.get_run_sources(connection, run_id) == original_sources
        report = storage.get_interactive_report(connection, sid)
        session = storage.get_agent_session(connection, sid)
        scoped = storage.agent_context_sources(
            connection, "SYNTHETIC", run_id, sid, report["turn_id"]
        )
        fetched = next(s for s in scoped if storage.is_agent_turn_source(s, sid, report["turn_id"]))
        assert storage.sha256_file(root / fetched["raw_path"]) == fetched["sha256"]
        # 其他 Turn 看不到本轮补取资料，也不能引用它。
        other = storage.agent_context_sources(connection, "SYNTHETIC", run_id, sid, "other-turn")
        assert fetched["id"] not in {s["id"] for s in other}
    reference = result["report"]["claims"][0]["evidence"][0]
    with pytest.raises(agent.AgentError, match="不属于当前"):
        agent._validate_evidence_refs(
            root, session, [EvidenceRef.model_validate(reference)], turn_id="other-turn"
        )
    saved = agent.memo_save(
        root,
        sid,
        MemoRequest(
            title="补取指标",
            content="两年平均 110",
            source_refs=[EvidenceRef.model_validate(reference)],
        ),
    )
    assert saved["memo"]["version"] == 1


def test_fetch_is_cached_and_visible_to_new_sql_snapshot_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path)
    row, turn_id = _turn_row(root)
    row = {**row, "turn_id": turn_id}
    provider = Provider()
    monkeypatch.setattr(futu_fundamentals, "make_provider", lambda: provider)
    sql = "SELECT fact_id, label, value_decimal FROM financial_facts ORDER BY label, period"
    before = query_workspace.run_readonly_sql(root, row, turn_id, sql)
    assert before["row_count"] == 2
    fetched = agent_data.fetch_financial_data(root, row, "statements", "自定义经营指标", limit=20)
    cached = agent_data.fetch_financial_data(root, row, "statements", "", limit=20)
    assert cached["cached"] is True and cached["fetched_source_id"] == fetched["fetched_source_id"]
    assert provider.calls == provider.closed == 1
    after = query_workspace.run_readonly_sql(root, row, turn_id, sql)
    assert after["row_count"] == 4 and after["dataset_hash"] != before["dataset_hash"]
    old_artifact = query_workspace.read_analysis_artifact(root, row, turn_id, before["analysis_id"])
    assert old_artifact["result"]["row_count"] == 2


def test_offline_turn_rejects_fetch_before_opening_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path)
    row, turn_id = _turn_row(root)
    with storage.open_db(root) as connection:
        connection.execute(
            "UPDATE agent_turns SET question_json = ? WHERE id = ?",
            (json.dumps({"question": "离线", "allow_network": False}), turn_id),
        )
    monkeypatch.setattr(
        futu_fundamentals, "make_provider", lambda: pytest.fail("offline network access")
    )
    with pytest.raises(analysis_workspace.AnalysisWorkspaceError) as error:
        agent_data.fetch_financial_data(
            root, {**row, "turn_id": turn_id}, "statements", "", limit=10
        )
    assert error.value.code == "network_disabled"


def test_provider_failure_is_returned_to_agent_and_context_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path)
    provider = Provider()

    def fail(_dataset: str, _ticker: str) -> dict:
        raise SourceError("opend_unavailable", "模拟未启动 OpenD")

    monkeypatch.setattr(provider, "fetch", fail)
    monkeypatch.setattr(futu_fundamentals, "make_provider", lambda: provider)
    calls = 0

    def decide(_root: Path, messages: list[dict[str, str]], _model: str) -> tuple[dict, int]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "type": "tool_call",
                "tool": "fetch_financial_data",
                "arguments": {"dataset": "statements"},
            }, 20
        assert "opend_unavailable" in messages[-1]["content"]
        return {
            "type": "final",
            "answer": "尚无所需指标。",
            "claims": [],
            "evidence_refs": [],
            "counterarguments": [],
            "gaps": ["富途无法连接，未获得数据"],
            "suggested_next_checks": [],
        }, 20

    monkeypatch.setattr(agent, "_api_call_for_root", decide)
    result = agent.ask(
        root,
        "SYNTHETIC",
        InteractiveQuestion(question="缺失指标"),
        tool_names=["fetch_financial_data"],
    )
    assert result["status"] == "completed" and provider.closed == 1


def test_generic_python_calculation_syntax_stays_in_isolated_workspace(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    row, turn_id = _turn_row(root)
    sql = "SELECT fact_id, value_decimal FROM financial_facts ORDER BY period"
    snippets = [
        "def analyze(rows):\n    return {'mean': sum(float(r['value_decimal']) for r in rows) / len(rows)}",
        (
            "def analyze(rows):\n    total = 0\n    for index, row in enumerate(rows):\n"
            "        total += float(row['value_decimal'])\n    return {'mean': total / len(rows)}"
        ),
        (
            "def analyze(rows):\n    values = [float(r['value_decimal']) for r in rows]\n"
            "    return {'mean': sum(values) / len(values)}"
        ),
    ]
    for code in snippets:
        selected = query_workspace.run_readonly_sql(root, row, turn_id, sql)
        result = query_workspace.run_analysis_function(
            root, row, turn_id, selected["analysis_id"], code
        )
        assert result["result"] == {"mean": 110.0}
    for forbidden in (
        "def analyze(rows):\n    return {'x': open('secret').read()}",
        "def analyze(rows):\n    return {'x': [getattr(r, '__class__') for r in rows]}",
        "def analyze(rows):\n    return {'x': eval('1+1')}",
    ):
        with pytest.raises(query_workspace.QueryWorkspaceError):
            query_workspace.validate_function(forbidden)


def test_sql_returns_actionable_column_diagnostic(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    row, turn_id = _turn_row(root)
    with pytest.raises(query_workspace.QueryWorkspaceError, match="no such column: missing_field"):
        query_workspace.run_readonly_sql(
            root, row, turn_id, "SELECT missing_field FROM financial_facts"
        )
