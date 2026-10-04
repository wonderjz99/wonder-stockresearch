from __future__ import annotations

import json
from pathlib import Path

import pytest

from wonder_stockresearch import chat, cli, skill_registry, storage, writer
from wonder_stockresearch.models import RoutePlan


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname='test-project'\nversion='0'\n", encoding="utf-8"
    )
    return root


def write_skill(
    root: Path,
    directory: str,
    *,
    name: str | None = None,
    description: str = "测试方法",
    body: str = "仅在执行时可见的正文。",
    manifest: dict[str, object] | None = None,
    resources: dict[str, str] | None = None,
) -> Path:
    skill_dir = root / "skills" / directory
    skill_dir.mkdir(parents=True)
    header_name = name or directory
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {header_name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    for path, content in (resources or {}).items():
        target = skill_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if manifest is not None:
        (skill_dir / "skill.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
    return skill_dir


def capability_manifest(name: str, *, dependencies: list[str] | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "capability",
        "version": "1",
        "applies_to": [name],
        "dependencies": dependencies or [],
        "capabilities": [name],
        "prompt_resources": [],
    }


def test_router_catalog_contains_only_skill_summaries(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write_skill(
        root,
        "method",
        description="路由可见的简介",
        body="ROUTER_UNAVAILABLE_BODY",
        manifest={
            "schema_version": 1,
            "kind": "method",
            "version": "1",
            "applies_to": ["fixed_research"],
            "dependencies": [],
            "capabilities": [],
            "prompt_resources": ["references/method.md"],
        },
        resources={"references/method.md": "ROUTER_UNAVAILABLE_REFERENCE"},
    )

    prompt = skill_registry.catalog_prompt(root)

    assert "路由可见的简介" in prompt
    assert "ROUTER_UNAVAILABLE_BODY" not in prompt
    assert "ROUTER_UNAVAILABLE_REFERENCE" not in prompt


def test_pure_method_is_discovered_loaded_on_demand_and_hashed(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    skill_dir = write_skill(root, "new-method", body="第一版方法正文")

    first = skill_registry.catalog(root)
    summary = first.skills["new-method"]
    loaded = skill_registry.load_selected(root, ["new-method"])

    assert summary.available is True
    assert summary.kind == "method"
    assert loaded[0].instructions == "第一版方法正文\n"
    (skill_dir / "SKILL.md").write_text(
        "---\nname: new-method\ndescription: 测试方法\n---\n\n第二版方法正文。\n",
        encoding="utf-8",
    )
    assert skill_registry.catalog(root).skills["new-method"].content_hash != summary.content_hash


def test_invalid_capability_path_duplicate_and_cycle_are_disabled(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write_skill(root, "unknown", manifest=capability_manifest("not_registered"))
    write_skill(
        root,
        "bad-path",
        manifest={
            "schema_version": 1,
            "kind": "method",
            "version": "1",
            "applies_to": [],
            "dependencies": [],
            "capabilities": [],
            "prompt_resources": ["../outside.md"],
        },
    )
    write_skill(root, "first", name="duplicate")
    write_skill(root, "second", name="duplicate")
    write_skill(root, "cycle-a", manifest=capability_manifest("fixed_research", dependencies=["cycle-b"]))
    write_skill(root, "cycle-b", manifest=capability_manifest("interactive_research", dependencies=["cycle-a"]))

    catalog = skill_registry.catalog(root)

    assert not catalog.skills["unknown"].available
    assert any(item.code == "unknown_capability" for item in catalog.skills["unknown"].diagnostics)
    assert not catalog.skills["bad-path"].available
    assert any(item.code == "invalid_resource_path" for item in catalog.skills["bad-path"].diagnostics)
    assert not catalog.skills["cycle-a"].available
    assert any(item.code == "dependency_cycle" for item in catalog.skills["cycle-a"].diagnostics)
    assert any(item.code == "duplicate_skill_name" for item in catalog.diagnostics)


def test_route_plan_keeps_legacy_empty_skills_and_rejects_duplicates() -> None:
    payload = {
        "intent": "clarify",
        "company_name": None,
        "ticker": None,
        "question": None,
        "focus": "general",
        "confidence": 0.5,
        "reason": "测试",
        "requires_confirmation": True,
    }
    assert RoutePlan.model_validate(payload).skill_names == []
    with pytest.raises(ValueError):
        RoutePlan.model_validate({**payload, "skill_names": ["a", "a"]})


def test_capability_selection_is_validated_from_catalog_without_loading_body(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write_skill(
        root,
        "interactive",
        body="不应由固定研究使用的正文",
        manifest=capability_manifest("interactive_research"),
    )

    with pytest.raises(skill_registry.SkillRegistryError, match="不适用于 capability"):
        skill_registry.validate_for_capability(root, ["interactive"], "fixed_research")


def test_cli_start_records_loaded_skill_metadata(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_root(tmp_path)
    write_skill(root, "method")
    write_skill(root, "fixed", manifest=capability_manifest("fixed_research", dependencies=["method"]))

    assert cli.main(["--root", str(root), "start", "SYNTHETIC", "--skill", "fixed"]) == 0

    result = json.loads(capsys.readouterr().out)
    run_id = result["data"]["run_id"]
    assert [item["name"] for item in result["data"]["skill_selection"]["skills"]] == ["method", "fixed"]
    with storage.open_db(root) as connection:
        steps = storage.list_steps(connection, run_id)
    assert steps[-1]["step_key"] == "skill_selection"
    assert json.loads(steps[-1]["metadata_json"])["skills"][1]["name"] == "fixed"


def test_writer_recovers_selected_skill_context_and_rejects_later_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_root(tmp_path)
    write_skill(root, "method", body="仅此一次的固定研究方法。")
    fixed_dir = write_skill(
        root,
        "fixed",
        body="固定研究执行说明。",
        manifest=capability_manifest("fixed_research", dependencies=["method"]),
    )
    assert cli.main(["--root", str(root), "start", "SYNTHETIC", "--skill", "fixed"]) == 0
    run_id = json.loads(capsys.readouterr().out)["data"]["run_id"]

    context, audit = writer._selected_skill_context(root, run_id)

    assert "仅此一次的固定研究方法。" in context
    assert [item["name"] for item in audit] == ["method", "fixed"]
    (fixed_dir / "SKILL.md").write_text(
        "---\nname: fixed\ndescription: 测试方法\n---\n\n已改变。\n", encoding="utf-8"
    )
    with pytest.raises(writer.WriterError, match="Skill 内容已在本次 run 创建后改变"):
        writer._selected_skill_context(root, run_id)


def test_chat_records_selected_skill_metadata_at_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_root(tmp_path)
    storage.initialize(root)
    write_skill(root, "method")
    write_skill(root, "interactive", manifest=capability_manifest("interactive_research", dependencies=["method"]))
    session_id = chat._new_session(root, "临时分析")
    row = chat._row(root, session_id)
    plan = RoutePlan(
        intent="interactive_question",
        company_name="教学公司",
        ticker="SYNTHETIC",
        question="请分析风险",
        confidence=0.9,
        reason="测试",
        skill_names=["interactive"],
    )
    captured: dict[str, object] = {}

    def fake_ask(*args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"session_id": None, "run_id": None, "status": "completed"}

    monkeypatch.setattr(chat.agent, "ask", fake_ask)

    chat._execute_interactive(root, session_id, row, plan, model=None, on_progress=None)

    with storage.open_db(root) as connection:
        saved = storage.get_chat_session(connection, session_id)
    assert saved is not None
    history = json.loads(saved["history_json"])
    assert any("Skill 执行加载" in item["content"] and "interactive" in item["content"] for item in history)
    assert "仅在执行时可见的正文。" in str(captured["skill_context"])
    assert [item["name"] for item in captured["skill_audit"]] == ["method", "interactive"]
