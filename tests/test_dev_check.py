"""开发检查工具的失败发现、留证与隔离边界；无真实业务/网络。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dev_check", ROOT / "scripts/dev_check.py")
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)


@pytest.fixture
def project(tmp_path):
    for name in ("models.py", *{v[0] for v in dev.CONSTANTS.values()}):
        source = ROOT / "src/wonder_stockresearch" / name
        target = tmp_path / "src/wonder_stockresearch" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (tmp_path / "docs").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "docs/technical-spec.md", tmp_path / "docs/technical-spec.md")
    return tmp_path


def test_current_entries_and_code_defaults_agree():
    assert dev.check_contract(ROOT)["ok"]
    assert dev.check_docs(ROOT)["ok"]
    archives = dev.check_archives(ROOT)
    assert archives["ok"]
    # 私有档案不随公开仓库分发；保留本地快照时仍要求完整的七份记录。
    expected = 7 if (ROOT / "docs/history/20261012-before-workflow").exists() else 0
    assert archives["files"] == expected


def test_public_docs_required_without_personal_development_files(tmp_path):
    for name in dev.ENTRY_DOCS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Public documentation\n")
    assert dev.check_docs(tmp_path)["ok"]
    (tmp_path / "docs/technical-spec.md").unlink()
    result = dev.check_docs(tmp_path)
    assert not result["ok"]
    assert "missing_document:docs/technical-spec.md" in result["issues"]


def test_present_personal_docs_are_checked_including_their_links(tmp_path):
    for name in dev.ENTRY_DOCS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Public documentation\n")
    (tmp_path / "PLAN.md").write_text("[bad reference](missing.md)\n")
    result = dev.check_docs(tmp_path)
    assert not result["ok"]
    assert "missing_link:PLAN.md:missing.md" in result["issues"]
    (tmp_path / "PLAN.md").write_text("[current rules](docs/technical-spec.md)\n")
    assert dev.check_docs(tmp_path)["ok"]


@pytest.mark.parametrize("replacement", ["59999", "60000 | dup |\n| token_budget | 60000"])
def test_contract_detects_wrong_or_duplicated_spec_default(project, replacement):
    spec = project / "docs/technical-spec.md"
    spec.write_text(
        spec.read_text().replace("| token_budget | 60000 |", f"| token_budget | {replacement} |")
    )
    result = dev.check_contract(project)
    assert not result["ok"] and any("token_budget" in i for i in result["issues"])


def test_contract_detects_code_change_without_business_import(project):
    path = project / "src/wonder_stockresearch/models.py"
    path.write_text(
        "raise RuntimeError('must not import')\n"
        + path.read_text().replace("Field(default=60000,", "Field(default=59000,")
    )
    assert not dev.check_contract(project)["ok"]


def test_docs_detect_missing_link_and_unclosed_fence(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "ENTRY_DOCS", ("README.md",))
    path = tmp_path / "README.md"
    path.write_text("[broken](missing.md)\n```python\nprint('x')\n")
    result = dev.check_docs(tmp_path)
    assert not result["ok"] and len(result["issues"]) == 2
    path.write_text("```text\n[fake example](absent.md)\n```\n[valid](README.md)\n")
    assert dev.check_docs(tmp_path)["ok"]


def test_archive_hash_change_is_reported(tmp_path):
    archive = tmp_path / "docs/history/snapshot"
    archive.mkdir(parents=True)
    (archive / "manifest.json").write_text(
        json.dumps(
            {"files": [{"archive_path": "docs/history/snapshot/old.md", "archive_sha256": "wrong"}]}
        )
    )
    (archive / "old.md").write_text("changed")
    assert not dev.check_archives(tmp_path)["ok"]


def test_child_environment_does_not_inherit_model_or_exporter_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-credential")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "fake-credential")
    monkeypatch.setenv("WONDER_TRACE_EXPORTER", "otlp")
    monkeypatch.setenv("WONDER_AGENT_RUNTIME", "pi")
    env = dev.check_environment()
    assert "DEEPSEEK_API_KEY" not in env and "LANGFUSE_SECRET_KEY" not in env
    assert "WONDER_TRACE_EXPORTER" not in env and env["WONDER_AGENT_RUNTIME"] == "legacy"


def test_failed_command_has_nonzero_result_and_persisted_log(tmp_path):
    output = tmp_path / "data/dev-checks/run"
    output.mkdir(parents=True)
    result = dev.run_step(
        tmp_path,
        output,
        "fail",
        [sys.executable, "-c", "print('diagnostic'); raise SystemExit(3)"],
        dev.check_environment(),
    )
    assert not result["ok"] and result["returncode"] == 3
    assert "diagnostic" in (tmp_path / result["log"]).read_text()


def test_check_report_preserves_failure_and_records_only_selected_commands(project, monkeypatch):
    commands = []

    def fake_step(root, output, name, command, env):
        commands.append(command)
        return {
            "name": name,
            "ok": name != "ruff",
            "returncode": 1 if name == "ruff" else 0,
            "command": command,
        }

    monkeypatch.setattr(dev, "run_step", fake_step)
    monkeypatch.setattr(dev, "check_docs", lambda root: {"ok": True})
    report = dev.run_checks(project, Namespace(full=False, tests=[], pi=False, node=False))
    assert not report["ok"]
    assert json.loads((project / report["report"]).read_text()) == report
    assert len(commands) == 2 and not any("pytest" in command for command in commands)
    assert not any("install" in command or "ask" in command for command in commands)


def test_output_cannot_escape_project_via_symlink(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "data").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="data"):
        dev.run_checks(root, Namespace(full=False, tests=[], pi=False, node=False))
    assert list(outside.iterdir()) == []


def test_main_returns_failure_as_json(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["dev_check.py", "check"])
    monkeypatch.setattr(dev, "run_checks", lambda root, args: {"ok": False, "checks": []})
    assert dev.main() == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "checks": []}


@pytest.mark.parametrize(
    "arguments",
    [
        ["--pi"],
        ["--full", "--tests", "tests/test_dev_check.py"],
        ["--tests", "../outside.py"],
        ["--tests", "README.md"],
    ],
)
def test_main_rejects_invalid_check_selection_before_running(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["dev_check.py", "check", *arguments])
    monkeypatch.setattr(dev, "run_checks", lambda *args: pytest.fail("must reject before running"))
    with pytest.raises(SystemExit) as error:
        dev.main()
    assert error.value.code == 2
