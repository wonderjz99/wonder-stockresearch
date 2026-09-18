from __future__ import annotations

import json
from pathlib import Path

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch.cli import main


def write_root_and_packet(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8")
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(copy_synthetic_packet()), encoding="utf-8")
    return root, packet_path


def test_validate_packet_returns_machine_readable_success(tmp_path: Path, capsys: object) -> None:
    """Changing successful validation to non-JSON or a failure exit code must fail here."""
    root, packet_path = write_root_and_packet(tmp_path)

    exit_code = main(["--root", str(root), "validate-packet", "--input", str(packet_path)])

    stdout = capsys.readouterr().out
    assert exit_code == 0
    assert json.loads(stdout) == {"ok": True, "data": {"valid": True}, "issues": []}


def test_render_packet_writes_only_under_project_data(tmp_path: Path, capsys: object) -> None:
    """Allowing an output outside data/ or skipping output creation must fail here."""
    root, packet_path = write_root_and_packet(tmp_path)
    output = root / "data" / "teaching-report.md"

    exit_code = main(
        [
            "--root",
            str(root),
            "render-packet",
            "--input",
            str(packet_path),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["data"]["output"] == "data/teaching-report.md"
    assert "离线样例/未正式归档" in output.read_text(encoding="utf-8")

    rejected_exit_code = main(
        [
            "--root",
            str(root),
            "render-packet",
            "--input",
            str(packet_path),
            "--output",
            str(tmp_path / "outside-data.md"),
        ]
    )

    assert rejected_exit_code == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
