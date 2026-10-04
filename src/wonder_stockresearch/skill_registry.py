"""独立 CLI 使用的轻量 Skill 注册表。

Skill 是模型上下文，不是权限系统；所有写入、评分和报告冻结规则仍由 Python 服务强制执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    instructions: str
    path: str


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


def discover(root: Path) -> dict[str, SkillDefinition]:
    result: dict[str, SkillDefinition] = {}
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return result
    for path in sorted(skills_root.glob("*/SKILL.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        metadata, instructions = _frontmatter(raw)
        name = metadata.get("name") or path.parent.name
        description = metadata.get("description", "")
        result[name] = SkillDefinition(
            name=name,
            description=description,
            instructions=instructions,
            path=path.relative_to(root).as_posix(),
        )
    return result


def prompt_context(root: Path) -> str:
    skills = discover(root)
    if not skills:
        return "当前项目没有可加载的 Skill；依据内置路由规则工作。"
    blocks = []
    for skill in skills.values():
        blocks.append(
            f"## Skill: {skill.name}\n描述：{skill.description}\n来源：{skill.path}\n"
            f"指令：\n{skill.instructions[:12000]}"
        )
    return "\n\n".join(blocks)
