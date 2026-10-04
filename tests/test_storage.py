from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import cli, research, storage
from wonder_stockresearch.cli import main
from wonder_stockresearch.models import ResearchPacket
from wonder_stockresearch.research import ResearchError


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def packet_for(ticker: str) -> ResearchPacket:
    data = copy_synthetic_packet()
    data["draft"]["ticker"] = ticker
    for source in data["sources"]:
        source["ticker"] = ticker
    return ResearchPacket.model_validate(data)


def completed_run(root: Path, ticker: str) -> str:
    started = research.start_run(root, ticker)
    run_id = str(started["run_id"])
    research.save_draft(root, run_id, packet_for(ticker))
    research.finish_run(root, run_id)
    return run_id


def test_saved_draft_survives_reconnect_and_validates(tmp_path: Path) -> None:
    """Dropping draft_json persistence or coverage checks must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    _, issues = research.save_draft(root, run_id, packet_for("SYNTHETIC"))
    assert issues == []

    status = research.get_status(root, run_id)
    assert status["status"] == "draft"
    assert status["answer_count"] == 24
    assert status["source_count"] == 2
    assert status["score_total"] is None

    assert research.validate_run(root, run_id) == []


def test_same_day_runs_do_not_collide_and_history_lists_both(tmp_path: Path) -> None:
    """Reusing a run directory name or hiding a run from history must fail this test."""
    root = make_root(tmp_path)
    first = completed_run(root, "SYNTHETIC")
    second = completed_run(root, "SYNTHETIC")

    first_dir = research.finish_run(root, first)["run_dir"]
    second_dir = research.finish_run(root, second)["run_dir"]
    assert first_dir != second_dir

    history = research.get_history(root, "SYNTHETIC")["runs"]
    assert {row["id"] for row in history} == {first, second}
    assert all(row["status"] == "completed" for row in history)


def test_completed_run_is_immutable_and_finish_is_idempotent(tmp_path: Path) -> None:
    """Overwriting a completed run or duplicating artifacts on retry must fail here."""
    root = make_root(tmp_path)
    run_id = completed_run(root, "SYNTHETIC")
    first = research.finish_run(root, run_id)

    again = research.finish_run(root, run_id)

    assert again["run_dir"] == first["run_dir"]
    assert again["artifacts"] == first["artifacts"]

    with pytest.raises(ResearchError) as exc_info:
        research.save_draft(root, run_id, packet_for("SYNTHETIC"))
    assert exc_info.value.code == "run_completed"
    assert exc_info.value.exit_code == 2


def test_invalid_draft_is_rejected_by_finish_and_stays_draft(tmp_path: Path) -> None:
    """Marking an incomplete draft completed must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    incomplete = copy_synthetic_packet()
    incomplete["draft"]["ticker"] = "SYNTHETIC"
    incomplete["draft"]["answers"] = incomplete["draft"]["answers"][:-1]
    packet = ResearchPacket.model_validate(incomplete)
    _, issues = research.save_draft(root, run_id, packet)
    assert any(issue.code == "missing_id" for issue in issues)

    with pytest.raises(ResearchError) as exc_info:
        research.finish_run(root, run_id)
    assert exc_info.value.exit_code == 2
    assert research.get_status(root, run_id)["status"] == "draft"


def test_baseline_from_another_stock_is_rejected(tmp_path: Path) -> None:
    """Accepting a cross-ticker baseline must fail this test."""
    root = make_root(tmp_path)
    baseline_run = completed_run(root, "HK.00728")

    with pytest.raises(ResearchError) as exc_info:
        research.start_run(root, "US.AAPL", baseline_run_id=baseline_run)
    assert exc_info.value.code == "baseline_ticker_mismatch"

    same_stock = research.start_run(root, "HK.00728", baseline_run_id=baseline_run)
    assert same_stock["baseline_run_id"] == baseline_run


def test_resume_only_works_from_failed(tmp_path: Path) -> None:
    """Allowing resume on a draft/completed run must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])

    with pytest.raises(ResearchError) as draft_error:
        research.resume_run(root, run_id)
    assert draft_error.value.code == "not_failed"

    with storage.open_db(root) as connection:
        storage.mark_run_failed(connection, run_id, "{}", storage.now_utc())
    assert research.get_status(root, run_id)["status"] == "failed"

    resumed = research.resume_run(root, run_id)
    assert resumed["status"] == "draft"


def test_artifact_write_failure_marks_failed_not_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Marking completed before artifacts are written must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet_for("SYNTHETIC"))

    def broken_write(path: Path, text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(storage, "write_atomic", broken_write)

    with pytest.raises(ResearchError) as exc_info:
        research.finish_run(root, run_id)
    assert exc_info.value.exit_code == 1
    assert research.get_status(root, run_id)["status"] == "failed"


def test_cli_watch_start_save_status_finish(tmp_path: Path, capsys: object) -> None:
    """Breaking the JSON contract or the run lifecycle must fail this test."""
    root = make_root(tmp_path)
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(copy_synthetic_packet(), ensure_ascii=False), encoding="utf-8")

    assert main(["--root", str(root), "watch", "add", "SYNTHETIC", "--name", "教学公司"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["watched"] is True

    assert main(["--root", str(root), "start", "SYNTHETIC"]) == 0
    run_id = json.loads(capsys.readouterr().out)["data"]["run_id"]

    exit_code = main(
        [
            "--root",
            str(root),
            "save-draft",
            run_id,
            "--input",
            str(packet_path),
        ]
    )
    assert exit_code == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["ok"] is True and saved["issues"] == []

    assert main(["--root", str(root), "status", run_id]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["status"] == "draft"

    assert main(["--root", str(root), "finish", run_id]) == 0
    finished = json.loads(capsys.readouterr().out)["data"]
    assert finished["status"] == "completed"
    assert (root / finished["run_dir"] / "company.md").is_file()

    assert main(["--root", str(root), "history", "SYNTHETIC"]) == 0
    assert len(json.loads(capsys.readouterr().out)["data"]["runs"]) == 1


def test_tty_finish_prints_paths_and_retry_summary_without_raw_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "workspace with spaces"
    outer.mkdir()
    root = make_root(outer)
    run_id = completed_run(root, "SYNTHETIC")
    # The second finish is the normal idempotent retry and omits derived fields.
    research.finish_run(root, run_id)

    class TerminalOutput:
        def __init__(self) -> None:
            self.value = ""

        def isatty(self) -> bool:
            return True

        def write(self, value: str) -> int:
            self.value += value
            return len(value)

        def flush(self) -> None:
            return None

    terminal = TerminalOutput()
    monkeypatch.setattr(cli.sys, "stdout", terminal)

    assert main(["--root", str(root), "finish", run_id]) == 0

    run_dir = next((root / "data" / "stocks" / "SYNTHETIC" / "runs").iterdir()).resolve()
    assert "正式报告已完成并冻结" in terminal.value
    assert "护城河总分：未评估 / 60" in terminal.value
    assert f"运行目录：{run_dir}" in terminal.value
    assert f"报告：{run_dir / 'company.md'}" in terminal.value
    assert f"结构化数据：{run_dir / 'research.json'}" in terminal.value
    assert "复评报告：" not in terminal.value
    assert "session_id" not in terminal.value
    assert '"run_id"' not in terminal.value


def test_finish_summary_handles_optional_review_and_null_score(tmp_path: Path) -> None:
    outer = tmp_path / "report folder with spaces"
    outer.mkdir()
    root = make_root(outer)
    run_dir = root / "data" / "stocks" / "SYNTHETIC" / "runs" / "sample run"
    run_dir.mkdir(parents=True)
    (run_dir / "company.md").write_text("report", encoding="utf-8")
    (run_dir / "research.json").write_text("{}", encoding="utf-8")
    result: dict[str, object] = {
        "status": "completed",
        "run_dir": str(run_dir.relative_to(root)),
        "score_total": None,
        "quality_status": "evidence_incomplete",
    }

    without_review = cli._finish_summary(root, result)
    assert "质量：证据不完整" in without_review
    assert "护城河总分：未评估 / 60" in without_review
    assert "复评报告：" not in without_review
    assert str(run_dir.resolve()) in without_review

    review_path = run_dir / "review.md"
    review_path.write_text("review", encoding="utf-8")
    with_review = cli._finish_summary(root, result)
    assert f"复评报告：{review_path.resolve()}" in with_review
