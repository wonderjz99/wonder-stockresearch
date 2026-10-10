"""工具分发重构的执行边界回归；不访问模型或 OpenD。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_company_agent import turn_row
from tests.test_storage import make_root
from wonder_stockresearch import storage
from wonder_stockresearch.interactive import agent_company, agent_tools
from wonder_stockresearch.interactive.agent_errors import AgentError


def test_invalid_arguments_are_rejected_before_any_storage_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("未通过输入校验的工具不能打开数据库")

    monkeypatch.setattr(storage, "open_db", forbidden)
    with pytest.raises(AgentError) as error:
        agent_tools.execute_tool(
            tmp_path, {"ticker": "SYNTHETIC"}, "read_source", {"source_id": "invalid"}, []
        )
    assert error.value.code == "invalid_tool_arguments"


def test_ungranted_tool_is_rejected_before_input_validation_or_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("未获授权的工具不能打开数据库")

    monkeypatch.setattr(storage, "open_db", forbidden)
    with pytest.raises(AgentError) as error:
        agent_tools.execute_tool(tmp_path, {"ticker": "SYNTHETIC"}, "run_analysis_code", {}, [])
    assert error.value.code == "unauthorized_tool"


@pytest.mark.parametrize("tool", ["get_run_status", "list_memos"])
def test_metadata_tools_do_not_eagerly_load_unrelated_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    root = make_root(tmp_path)
    storage.initialize(root)

    def forbidden(*args, **kwargs):
        pytest.fail("状态和 Memo 目录不需要读取来源列表")

    monkeypatch.setattr(agent_company, "context_sources", forbidden)
    result = agent_tools.execute_tool(
        root, {"id": "offline-session", "ticker": "SYNTHETIC", "run_id": None}, tool, {}, []
    )
    assert result["ticker"] == "SYNTHETIC"


@pytest.mark.parametrize("tool", ["get_run_status", "list_memos"])
def test_metadata_tools_still_reject_a_turn_from_another_company(
    tmp_path: Path,
    tool: str,
) -> None:
    root = make_root(tmp_path)
    row = {**turn_row(root), "ticker": "US.OTHER"}

    with pytest.raises(AgentError) as error:
        agent_tools.execute_tool(root, row, tool, {}, [])
    assert error.value.code == "unknown_agent_turn"
