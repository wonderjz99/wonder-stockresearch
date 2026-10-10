from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import storage
from wonder_stockresearch.fixed import research
from wonder_stockresearch.fixed.research import ResearchError
from wonder_stockresearch.models import ResearchPacket, SourceRequest


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def packet_for(ticker: str = "SYNTHETIC") -> ResearchPacket:
    data = copy_synthetic_packet()
    data["draft"]["ticker"] = ticker
    for source in data["sources"]:
        source["ticker"] = ticker
    return ResearchPacket.model_validate(data)


def modified_packet() -> ResearchPacket:
    data = copy_synthetic_packet()
    for moat in data["draft"]["moats"]:
        if moat["dimension_id"] == "M1":
            moat["score"] = 9
        if moat["dimension_id"] == "M3":
            moat["score"] = 5
    for gate in data["draft"]["gates"]:
        if gate["gate_id"] == "G2":
            gate["result"] = "fail"
    for answer in data["draft"]["answers"]:
        if answer["question_id"] == "Q02":
            answer["status"] = "partial"
            answer["gap"] = {
                "missing": "行业十年趋势",
                "where_to_find": "历史行业统计",
                "next_check": "下一份年报",
                "impact": "无法判断长期需求",
            }
    data["draft"]["review_explanations"] = [
        {
            "target_id": "M3",
            "statement": {
                "id": "rev-m3",
                "kind": "inference",
                "text": "M3 由未评估改为 5，因为 2025 年报出现转换成本线索。",
                "evidence": [],
                "numbers": [],
            },
        }
    ]
    return ResearchPacket.model_validate(data)


def completed_run(
    root: Path, ticker: str = "SYNTHETIC", packet: ResearchPacket | None = None
) -> str:
    run_id = str(research.start_run(root, ticker)["run_id"])
    research.save_draft(root, run_id, packet if packet is not None else packet_for(ticker))
    research.finish_run(root, run_id)
    return run_id


def run_dir(root: Path, run_id: str) -> Path:
    with storage.open_db(root) as connection:
        run = storage.get_run(connection, run_id)
    assert run is not None
    return storage.run_dir(root, run["ticker"], storage.parse_iso(run["created_at"]), run_id)


def test_finish_with_baseline_writes_review_and_keeps_baseline(tmp_path: Path) -> None:
    """Overwriting the baseline report or skipping review.md must fail this test."""
    root = make_root(tmp_path)
    baseline = completed_run(root)
    baseline_company = (run_dir(root, baseline) / "company.md").read_bytes()
    with storage.open_db(root) as connection:
        baseline_hashes = json.loads(storage.get_run(connection, baseline)["artifact_hashes_json"])

    current = str(research.start_run(root, "SYNTHETIC", baseline_run_id=baseline)["run_id"])
    research.save_draft(root, current, modified_packet())
    result = research.finish_run(root, current)

    assert "review.md" in result["artifacts"]
    review = (run_dir(root, current) / "review.md").read_text(encoding="utf-8")
    assert "# 公司研究复评" in review
    assert "M3 由未评估改为 5" in review
    assert "差值" in review

    assert (run_dir(root, baseline) / "company.md").read_bytes() == baseline_company
    with storage.open_db(root) as connection:
        assert (
            json.loads(storage.get_run(connection, baseline)["artifact_hashes_json"])
            == baseline_hashes
        )
    assert research.finish_run(root, baseline)["status"] == "completed"


def test_compare_requires_baseline(tmp_path: Path) -> None:
    """Allowing compare without a baseline must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet_for())

    with pytest.raises(ResearchError) as exc_info:
        research.compare_runs(root, run_id)
    assert exc_info.value.code == "no_baseline"


def test_compare_reports_diffs_and_missing_score_is_not_applicable(tmp_path: Path) -> None:
    """Turning an unknown score into a number or hiding gate/answer changes must fail here."""
    root = make_root(tmp_path)
    baseline = completed_run(root)
    current = str(research.start_run(root, "SYNTHETIC", baseline_run_id=baseline)["run_id"])
    research.save_draft(root, current, modified_packet())

    comparison = research.compare_runs(root, current)

    assert comparison["comparable"] is True
    assert comparison["score_total"]["baseline"] is None
    assert comparison["score_total"]["current"] == 31
    assert comparison["score_total"]["diff"] is None
    moats = {item["dimension_id"]: item for item in comparison["moats"]}
    assert moats["M1"]["diff"] == 1
    assert moats["M3"]["baseline_score"] is None
    assert moats["M3"]["diff"] is None
    gates = {item["gate_id"]: item for item in comparison["gates"]}
    assert gates["G2"]["changed"] is True
    answers = {item["question_id"]: item for item in comparison["answers"]}
    assert answers["Q02"]["changed"] is True
    assert comparison["changes"]["moats_changed"] == 2


def test_compare_separates_new_evidence_from_reused(tmp_path: Path) -> None:
    """Presenting old baseline evidence as newly acquired must fail this test."""
    root = make_root(tmp_path)
    baseline = completed_run(root)
    current = str(research.start_run(root, "SYNTHETIC", baseline_run_id=baseline)["run_id"])
    document = tmp_path / "new-evidence.txt"
    document.write_text("本次新增证据\n", encoding="utf-8")
    added = research.source_add(
        root,
        current,
        SourceRequest(
            kind="primary",
            title="本次新增证据",
            file=str(document),
            published_at=datetime(2026, 9, 15, tzinfo=UTC),
            publication_precision="datetime",
        ),
    )
    research.save_draft(root, current, packet_for())

    comparison = research.compare_runs(root, current)
    evidence = comparison["evidence"]

    assert str(added["source_id"]) in evidence["new_source_ids"]
    assert str(added["source_id"]) not in evidence["reused_source_ids"]
    assert len(evidence["reused_source_ids"]) == 2
    new_source = evidence["new_sources"][0]
    assert new_source["retrieved_at"]
    assert new_source["published_at"].startswith("2026-09-15")


def test_backup_and_restore_verify_database_sources_and_artifacts(tmp_path: Path) -> None:
    """Skipping the SQLite snapshot, file copies, or hashes must fail this test."""
    root = make_root(tmp_path)
    baseline = completed_run(root)
    current = str(research.start_run(root, "SYNTHETIC", baseline_run_id=baseline)["run_id"])
    research.save_draft(root, current, modified_packet())
    research.finish_run(root, current)

    result = research.backup(root, "data/backups/b1")

    assert result["file_count"] > 0
    assert result["missing"] == []
    backup_dir = root / "data" / "backups" / "b1"
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    assert (backup_dir / "wonder.db").is_file()
    assert (backup_dir / "RESTORE.md").is_file()

    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / "pyproject.toml").write_text("[project]\nname='r'\nversion='0'\n", encoding="utf-8")
    shutil.copy2(backup_dir / "wonder.db", _ensure_parent(restored / "data" / "wonder.db"))
    for entry in manifest["files"]:
        target = restored / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_dir / "files" / entry["path"], target)
        assert storage.sha256_file(target) == entry["sha256"]

    storage.initialize(restored)
    status = research.get_status(restored, current)
    assert status["status"] == "completed"
    assert research.finish_run(restored, current)["artifacts"].get("review.md")
    assert research.finish_run(restored, baseline)["status"] == "completed"

    with pytest.raises(ResearchError) as exc_info:
        research.backup(root, "data/backups/b1")
    assert exc_info.value.code == "backup_exists"

    with pytest.raises(ResearchError) as invalid:
        research.backup(root, "data/not-backups")
    assert invalid.value.code == "invalid_backup_output"


def test_backup_respects_project_write_lock(tmp_path: Path) -> None:
    """Backing up without the project lock, or while another writer holds it, must fail here."""
    root = make_root(tmp_path)
    completed_run(root)

    with storage.project_lock(root), pytest.raises(storage.StorageError) as exc_info:
        research.backup(root, "data/backups/locked", lock_timeout=0.2)
    assert exc_info.value.code == "lock_timeout"
    assert not (root / "data" / "backups" / "locked").exists()


def _ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
