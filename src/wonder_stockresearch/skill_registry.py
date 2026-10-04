"""项目内 Skill Catalog、按需加载和 capability 约束。

启动和 Router 只处理可用性摘要。只有某个受控执行路径选择 Skill 后，才会读取
SKILL.md 正文及其声明的参考资料。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import capabilities


class SkillRegistryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    kind: Literal["method", "capability"] = "method"
    version: str = Field(default="1", min_length=1, max_length=100)
    applies_to: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    prompt_resources: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SkillDiagnostic:
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass
class SkillSummary:
    name: str
    description: str
    path: str
    directory: Path
    kind: Literal["method", "capability"]
    version: str
    applies_to: tuple[str, ...]
    dependencies: tuple[str, ...]
    capabilities: tuple[str, ...]
    prompt_resources: tuple[str, ...]
    content_hash: str
    diagnostics: list[SkillDiagnostic] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return not self.diagnostics

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "kind": self.kind,
            "version": self.version,
            "applies_to": list(self.applies_to),
            "dependencies": list(self.dependencies),
            "capabilities": list(self.capabilities),
            "content_hash": self.content_hash,
            "available": self.available,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class LoadedSkill:
    summary: SkillSummary
    instructions: str
    resources: tuple[tuple[str, str], ...]

    def audit_dict(self) -> dict[str, object]:
        return {
            "name": self.summary.name,
            "version": self.summary.version,
            "content_hash": self.summary.content_hash,
            "kind": self.summary.kind,
            "capabilities": list(self.summary.capabilities),
        }


@dataclass(frozen=True)
class SkillCatalog:
    skills: dict[str, SkillSummary]
    diagnostics: tuple[SkillDiagnostic, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "skills": [self.skills[name].to_dict() for name in sorted(self.skills)],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) != 3:
        return {}, text
    values: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values, parts[2].lstrip("\n")


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _append(summary: SkillSummary, code: str, message: str) -> None:
    if not any(item.code == code and item.message == message for item in summary.diagnostics):
        summary.diagnostics.append(SkillDiagnostic(code, message, summary.path))


def _manifest_for(directory: Path, summary: SkillSummary) -> _Manifest:
    path = directory / "skill.json"
    if not path.exists():
        return _Manifest()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        manifest = _Manifest.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        _append(summary, "invalid_manifest", f"skill.json 不符合 schema：{error}")
        return _Manifest()
    if manifest.schema_version != 1:
        _append(summary, "unsupported_manifest_version", "skill.json.schema_version 只能为 1")
    return manifest


def _safe_resource(directory: Path, value: str) -> Path | None:
    path = Path(value)
    if path.is_absolute():
        return None
    candidate = (directory / path).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _content_hash(raw_skill: str, resources: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    digest.update(raw_skill.encode("utf-8"))
    for name, content in sorted(resources):
        digest.update(b"\0")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content.encode("utf-8"))
    return digest.hexdigest()


def catalog(root: Path) -> SkillCatalog:
    """扫描元数据与可用性，不把正文或参考资料保留给 Router。"""
    skills_root = root / "skills"
    candidates: list[SkillSummary] = []
    global_diagnostics: list[SkillDiagnostic] = []
    if not skills_root.is_dir():
        return SkillCatalog({}, ())

    for path in sorted(skills_root.glob("*/SKILL.md")):
        relative = _relative(root, path)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            global_diagnostics.append(SkillDiagnostic("skill_read_error", str(error), relative))
            continue
        metadata, _ = _frontmatter(raw)
        name = metadata.get("name") or path.parent.name
        description = metadata.get("description", "").strip()
        summary = SkillSummary(
            name=name,
            description=description,
            path=relative,
            directory=path.parent,
            kind="method",
            version="1",
            applies_to=(),
            dependencies=(),
            capabilities=(),
            prompt_resources=(),
            content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )
        if not description:
            _append(summary, "missing_description", "SKILL.md frontmatter 必须提供 description")
        manifest = _manifest_for(path.parent, summary)
        summary.kind = manifest.kind
        summary.version = manifest.version
        summary.applies_to = tuple(manifest.applies_to)
        summary.dependencies = tuple(manifest.dependencies)
        summary.capabilities = tuple(manifest.capabilities)
        summary.prompt_resources = tuple(manifest.prompt_resources)
        if len(set(summary.dependencies)) != len(summary.dependencies):
            _append(summary, "duplicate_dependency", "dependencies 不能重复")
        if len(set(summary.capabilities)) != len(summary.capabilities):
            _append(summary, "duplicate_capability", "capabilities 不能重复")
        if summary.kind == "method" and summary.capabilities:
            _append(summary, "method_declares_capability", "method Skill 不能声明 capabilities")
        if summary.kind == "capability" and not summary.capabilities:
            _append(summary, "capability_missing_binding", "capability Skill 必须声明至少一个 capability")
        resources: list[tuple[str, str]] = []
        for resource in summary.prompt_resources:
            resource_path = _safe_resource(path.parent, resource)
            if resource_path is None:
                _append(summary, "invalid_resource_path", f"prompt_resources 路径不可用或越出 Skill 目录：{resource}")
                continue
            try:
                resources.append((resource, resource_path.read_text(encoding="utf-8")))
            except OSError as error:
                _append(summary, "resource_read_error", f"prompt_resources 无法读取：{resource}（{error}）")
        summary.content_hash = _content_hash(raw, resources)
        for name_value in summary.capabilities:
            capability = capabilities.get(name_value)
            if capability is None:
                _append(summary, "unknown_capability", f"未注册 capability：{name_value}")
            elif not capability.available:
                _append(summary, "capability_unavailable", f"capability 尚不可用：{name_value}")
        candidates.append(summary)

    by_name: dict[str, list[SkillSummary]] = {}
    for item in candidates:
        by_name.setdefault(item.name, []).append(item)
    skills: dict[str, SkillSummary] = {}
    for name, matches in by_name.items():
        if len(matches) != 1:
            for item in matches:
                _append(item, "duplicate_skill_name", f"重复的 Skill 名称：{name}")
                global_diagnostics.extend(item.diagnostics)
            continue
        skills[name] = matches[0]

    def visit(name: str, stack: tuple[str, ...]) -> None:
        item = skills[name]
        for dependency in item.dependencies:
            target = skills.get(dependency)
            if target is None:
                _append(item, "unknown_dependency", f"未发现依赖 Skill：{dependency}")
                continue
            if dependency in stack:
                cycle = stack[stack.index(dependency):] + (dependency,)
                for cycle_name in cycle:
                    if cycle_name in skills:
                        _append(skills[cycle_name], "dependency_cycle", "依赖循环：" + " → ".join(cycle))
                continue
            visit(dependency, (*stack, dependency))
            if not target.available:
                _append(item, "dependency_unavailable", f"依赖 Skill 不可用：{dependency}")

    for name in sorted(skills):
        visit(name, (name,))
    return SkillCatalog(skills, tuple(global_diagnostics))


def catalog_prompt(root: Path) -> str:
    """Router 的唯一 Skill 上下文：只有摘要，没有正文和 references。"""
    data = catalog(root)
    summaries = [
        {
            "name": item.name,
            "description": item.description,
            "path": item.path,
            "kind": item.kind,
            "applies_to": list(item.applies_to),
            "available": item.available,
        }
        for item in (data.skills[name] for name in sorted(data.skills))
    ]
    return json.dumps({"skills": summaries}, ensure_ascii=False, separators=(",", ":"))


def resolve_selected(root: Path, names: list[str]) -> tuple[SkillSummary, ...]:
    """用 catalog 验证直接选择及依赖，依赖在返回值中排在其调用方之前。"""
    if len(names) != len(set(names)):
        raise SkillRegistryError("duplicate_skill_selection", "同一次选择不能重复指定同一个 Skill")
    entries = catalog(root).skills
    ordered: list[SkillSummary] = []
    visiting: set[str] = set()
    selected: set[str] = set()

    def resolve(name: str) -> None:
        item = entries.get(name)
        if item is None:
            raise SkillRegistryError("unknown_skill", f"未发现 Skill：{name}")
        if not item.available:
            details = "；".join(diagnostic.message for diagnostic in item.diagnostics)
            raise SkillRegistryError("skill_unavailable", f"Skill 不可用：{name}（{details}）")
        if name in selected:
            return
        if name in visiting:
            raise SkillRegistryError("dependency_cycle", f"Skill 依赖循环：{name}")
        visiting.add(name)
        for dependency in item.dependencies:
            resolve(dependency)
        visiting.remove(name)
        selected.add(name)
        ordered.append(item)

    for name in names:
        resolve(name)
    return tuple(ordered)


def load_selected(root: Path, names: list[str]) -> tuple[LoadedSkill, ...]:
    """在受控执行开始时加载选中 Skill 与其目录内 references。"""
    loaded: list[LoadedSkill] = []
    for summary in resolve_selected(root, names):
        skill_path = root / summary.path
        raw = skill_path.read_text(encoding="utf-8")
        _, instructions = _frontmatter(raw)
        resources: list[tuple[str, str]] = []
        for resource in summary.prompt_resources:
            path = _safe_resource(summary.directory, resource)
            if path is None:
                raise SkillRegistryError("invalid_resource_path", f"Skill 资源不可读取：{resource}")
            try:
                resources.append((resource, path.read_text(encoding="utf-8")))
            except OSError as error:
                raise SkillRegistryError("resource_read_error", f"Skill 资源无法读取：{resource}（{error}）") from error
        current_hash = _content_hash(raw, resources)
        if current_hash != summary.content_hash:
            raise SkillRegistryError("skill_changed_during_load", f"Skill 载入时内容已变化：{summary.name}")
        loaded.append(LoadedSkill(summary, instructions, tuple(resources)))
    return tuple(loaded)


def validate_for_capability(root: Path, names: list[str], capability_name: str) -> tuple[SkillSummary, ...]:
    """仅凭 Catalog 元数据确认声明与当前受控执行能力相符。"""
    selected = resolve_selected(root, names)
    for summary in selected:
        if summary.applies_to and capability_name not in summary.applies_to:
            raise SkillRegistryError(
                "skill_capability_mismatch",
                f"Skill {summary.name} 不适用于 capability：{capability_name}",
            )
        if summary.capabilities and capability_name not in summary.capabilities:
            raise SkillRegistryError(
                "skill_capability_mismatch",
                f"Skill {summary.name} 声明的 capability 不包含：{capability_name}",
            )
    return selected


def load_for_capability(root: Path, names: list[str], capability_name: str) -> tuple[LoadedSkill, ...]:
    """在执行边界验证并加载 Skill 正文/参考资料。"""
    validate_for_capability(root, names, capability_name)
    return load_selected(root, names)


def selection_audit(loaded: tuple[LoadedSkill, ...]) -> list[dict[str, object]]:
    return [item.audit_dict() for item in loaded]


def execution_context(loaded: tuple[LoadedSkill, ...]) -> str:
    """提供给受控执行路径的正文；Router 永远不调用此函数。"""
    blocks: list[str] = []
    for item in loaded:
        blocks.append(f"## Skill: {item.summary.name}\n{item.instructions}")
        blocks.extend(f"### Reference: {name}\n{text}" for name, text in item.resources)
    return "\n\n".join(blocks)


def prompt_context(root: Path) -> str:
    """兼容旧调用方；阶段 17 起仅返回 catalog 摘要。"""
    return catalog_prompt(root)
