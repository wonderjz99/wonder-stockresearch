from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import research, storage
from wonder_stockresearch.models import ResearchPacket, Source, SourceRequest
from wonder_stockresearch.research import ResearchError, validate_packet


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def packet_with_primary(mutator) -> ResearchPacket:
    data = copy_synthetic_packet()
    mutator(data["sources"][0])
    return ResearchPacket.model_validate(data)


def _replace_source_ids(node: object, new_id: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "source_id":
                node[key] = new_id
            else:
                _replace_source_ids(value, new_id)
    elif isinstance(node, list):
        for item in node:
            _replace_source_ids(item, new_id)


def test_unknown_publication_date_primary_cannot_support_core_conclusions() -> None:
    """Letting an undated primary source support core claims must fail this test."""

    def mutate(source: dict[str, object]) -> None:
        source["published_at"] = None
        source["publication_precision"] = "unknown"

    issues = validate_packet(packet_with_primary(mutate))

    ineligible = [issue for issue in issues if issue.code == "ineligible_as_of_support"]
    assert ineligible
    assert all(issue.code != "missing_authoritative_support" for issue in issues)
    locations = {issue.location for issue in ineligible}
    assert any(location.startswith("draft.answers") for location in locations)
    assert any(location.startswith("draft.gates") for location in locations)
    assert any(location.startswith("draft.moats") for location in locations)


def test_provider_observed_after_as_of_cannot_support_core_conclusions() -> None:
    """Ignoring observed_after_as_of on a provider source must fail this test."""

    def mutate(source: dict[str, object]) -> None:
        source["kind"] = "provider"
        source["published_at"] = None
        source["publication_precision"] = "unknown"
        source["metadata"] = {"observed_after_as_of": True}

    codes = {issue.code for issue in validate_packet(packet_with_primary(mutate))}

    assert "ineligible_as_of_support" in codes


def test_provider_without_late_observation_flag_is_accepted() -> None:
    """Rejecting an as_of-eligible provider snapshot must fail this test."""

    def mutate(source: dict[str, object]) -> None:
        source["kind"] = "provider"
        source["published_at"] = None
        source["publication_precision"] = "unknown"
        source["metadata"] = {
            "observed_after_as_of": False,
            "observed_at": "2026-09-01T00:00:00+00:00",
        }

    assert validate_packet(packet_with_primary(mutate)) == []


def test_provider_observed_at_after_as_of_cannot_support() -> None:
    """Trusting only the observed_after_as_of flag and ignoring observed_at must fail here."""

    def mutate(source: dict[str, object]) -> None:
        source["kind"] = "provider"
        source["published_at"] = None
        source["publication_precision"] = "unknown"
        source["metadata"] = {
            "observed_after_as_of": False,
            "observed_at": "2026-09-20T00:00:00+00:00",
        }

    codes = {issue.code for issue in validate_packet(packet_with_primary(mutate))}

    assert "ineligible_as_of_support" in codes


def test_provider_without_observed_at_cannot_support() -> None:
    """Letting a provider source with no observation time support core claims must fail here."""

    def mutate(source: dict[str, object]) -> None:
        source["kind"] = "provider"
        source["published_at"] = None
        source["publication_precision"] = "unknown"
        source["metadata"] = {}

    codes = {issue.code for issue in validate_packet(packet_with_primary(mutate))}

    assert "ineligible_as_of_support" in codes


def test_date_only_primary_uses_local_end_of_day() -> None:
    """Treating a date-only publication as available before day end must fail this test."""
    data = copy_synthetic_packet()
    data["sources"][0]["published_at"] = "2026-09-10T00:00:00+00:00"
    data["sources"][0]["publication_precision"] = "date"
    data["draft"]["as_of"] = "2026-09-10T12:00:00+00:00"

    same_day = validate_packet(ResearchPacket.model_validate(data))
    assert "ineligible_as_of_support" in {issue.code for issue in same_day}

    data["draft"]["as_of"] = "2026-09-11T00:00:00+00:00"
    next_day = validate_packet(ResearchPacket.model_validate(data))
    assert next_day == []


def test_future_primary_is_both_future_source_and_ineligible_support() -> None:
    """Using a post-as_of primary source as support must fail this test."""

    def mutate(source: dict[str, object]) -> None:
        source["published_at"] = "2030-01-01T00:00:00+00:00"

    codes = {issue.code for issue in validate_packet(packet_with_primary(mutate))}

    assert "future_source" in codes
    assert "ineligible_as_of_support" in codes


def test_context_relation_does_not_satisfy_core_support() -> None:
    """Counting a context-only reference as support must fail this test."""
    data = copy_synthetic_packet()

    def downgrade(node: object) -> None:
        if isinstance(node, dict):
            if node.get("relation") == "supports":
                node["relation"] = "context"
            for value in node.values():
                downgrade(value)
        elif isinstance(node, list):
            for item in node:
                downgrade(item)

    downgrade(data["draft"])
    codes = {issue.code for issue in validate_packet(ResearchPacket.model_validate(data))}

    assert "missing_authoritative_support" in codes


def _archived_evidence_source(root: Path, run_id: str, tmp_path: Path) -> Source:
    document = tmp_path / "evidence.txt"
    document.write_text("可核验证据\n", encoding="utf-8")
    added = research.source_add(
        root,
        run_id,
        SourceRequest(
            kind="primary",
            title="证据",
            file=str(document),
            published_at=datetime(2026, 9, 1, tzinfo=UTC),
            publication_precision="datetime",
        ),
    )
    with storage.open_db(root) as connection:
        row = storage.get_source(connection, str(added["source_id"]))
    assert row is not None
    return Source.model_validate_json(row["source_json"])


def test_save_draft_merges_sources_already_linked_to_run(tmp_path: Path) -> None:
    """Requiring collected/archived sources to be re-declared must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    archived = _archived_evidence_source(root, run_id, tmp_path)

    data = copy_synthetic_packet()
    _replace_source_ids(data["draft"], str(archived.id))
    data["sources"] = []
    _, issues = research.save_draft(root, run_id, ResearchPacket.model_validate(data))

    assert issues == []
    assert research.validate_run(root, run_id) == []


def test_save_draft_still_flags_truly_unknown_source(tmp_path: Path) -> None:
    """Merging linked sources must not hide references to sources not in the run."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    data = copy_synthetic_packet()
    data["sources"] = []

    _, issues = research.save_draft(root, run_id, ResearchPacket.model_validate(data))

    assert "unknown_source" in {issue.code for issue in issues}


def test_undated_archived_source_blocks_finish_but_is_kept(tmp_path: Path) -> None:
    """Deleting the archived source or allowing finish must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    document = tmp_path / "memo.txt"
    document.write_text("可核验但未记录发布日期的证据\n", encoding="utf-8")
    added = research.source_add(
        root, run_id, SourceRequest(kind="primary", title="证据", file=str(document))
    )
    with storage.open_db(root) as connection:
        row = storage.get_source(connection, str(added["source_id"]))
    assert row is not None
    archived = Source.model_validate_json(row["source_json"])

    data = copy_synthetic_packet()
    _replace_source_ids(data["draft"], str(archived.id))
    data["sources"] = [json.loads(archived.model_dump_json())]
    research.save_draft(root, run_id, ResearchPacket.model_validate(data))

    assert "ineligible_as_of_support" in {issue.code for issue in research.validate_run(root, run_id)}
    assert (root / archived.raw_path).is_file()
    assert research.get_status(root, run_id)["source_count"] == 1
    with pytest.raises(ResearchError):
        research.finish_run(root, run_id)
