from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import envfile, research, writer
from wonder_stockresearch.models import ResearchPacket


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


def test_load_env_file_parses_and_does_not_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Overriding shell variables or mis-parsing quotes/comments must fail this test."""
    keys = ("WONDER_TEST_ENV_A", "WONDER_TEST_ENV_B", "WONDER_TEST_ENV_C")
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WONDER_TEST_ENV_B", "from-shell")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment line\n\n"
        "WONDER_TEST_ENV_A=value-a\n"
        "WONDER_TEST_ENV_B=from-file\n"
        "export WONDER_TEST_ENV_C='quoted value'\n"
        "not a valid line\n",
        encoding="utf-8",
    )

    loaded = envfile.load_env_file(env_path)

    assert os.environ["WONDER_TEST_ENV_A"] == "value-a"
    assert os.environ["WONDER_TEST_ENV_B"] == "from-shell"
    assert os.environ["WONDER_TEST_ENV_C"] == "quoted value"
    assert loaded == 2
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_load_env_file_missing_is_noop(tmp_path: Path) -> None:
    """A missing .env must not raise."""
    assert envfile.load_env_file(tmp_path / "does-not-exist.env") == 0


def test_writer_reads_root_dotenv_without_leaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ignoring root .env, or leaking its secret, must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.save_draft(root, run_id, packet_for())
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (root / ".env").write_text('DEEPSEEK_API_KEY="sk-from-dotenv"\n', encoding="utf-8")
    monkeypatch.setattr(
        writer,
        "_request_deepseek",
        lambda **kwargs: {
            "choices": [{"message": {"content": json.dumps(copy_synthetic_packet()["draft"])}}],
            "usage": {"total_tokens": 5},
        },
    )
    try:
        data, issues = writer.write_draft(root, run_id, strategy="one-shot")
        assert issues == []
        assert data["saved"] is True
        steps = research.get_status(root, run_id)["steps"]
        assert all("sk-from-dotenv" not in str(step) for step in steps)
    finally:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def test_dotenv_is_gitignored() -> None:
    """Committing the local .env must fail this test."""
    project_root = Path(__file__).resolve().parents[1]
    lines = {line.strip() for line in (project_root / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert ".env" in lines
    assert (project_root / ".env.example").is_file()
