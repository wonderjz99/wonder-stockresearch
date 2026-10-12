"""本地开发检查；不导入业务模块、不加载.env、不运行真实评测。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ENTRY_DOCS = (
    "README.md",
    "AGENTS.md",
    "PLAN.md",
    "PROGRESS.md",
    "docs/research-spec.md",
    "docs/technical-spec.md",
    "docs/teaching-contract.md",
    "docs/development-workflow.md",
    "docs/change-template.md",
    "docs/decisions.md",
)
CONSTANTS = {
    "wonder_schema": ("storage.py", "SCHEMA_VERSION"),
    "company_schema": ("company_data.py", "SCHEMA_VERSION"),
    "writer_max_calls": ("fixed/writer.py", "MAX_MODEL_CALLS"),
    "sql_attempts_per_turn": ("interactive/query_workspace.py", "MAX_SQL_ATTEMPTS_PER_TURN"),
    "function_executions_per_turn": (
        "interactive/query_workspace.py",
        "MAX_FUNCTION_EXECUTIONS_PER_TURN",
    ),
    "financial_record_limit": ("futu_fundamentals.py", "FINANCIAL_RECORD_LIMIT"),
}


def source_contract(root: Path) -> dict[str, int]:
    """只解析指定源码常量；遇到非字面量默认值就报告需人工适配。"""
    package = root / "src/wonder_stockresearch"
    tree = ast.parse((package / "models.py").read_text(encoding="utf-8"))
    question = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "InteractiveQuestion"
    )
    values = {}
    for node in question.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id
            in {"token_budget", "max_iterations", "input_token_limit", "output_token_limit"}
        ):
            value = next(k.value for k in node.value.keywords if k.arg == "default")
            values[node.target.id] = ast.literal_eval(value)
    for key, (filename, name) in CONSTANTS.items():
        tree = ast.parse((package / filename).read_text(encoding="utf-8"))
        node = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
        )
        values[key] = ast.literal_eval(node.value)
    if len(values) != 10 or any(type(v) is not int for v in values.values()):
        raise ValueError("默认值提取不完整或类型改变；需核对脚本与现行契约")
    return values


def check_contract(root: Path) -> dict:
    actual = source_contract(root)
    spec = (root / "docs/technical-spec.md").read_text(encoding="utf-8")
    issues = []
    for key, value in actual.items():
        rows = re.findall(rf"^\|\s*{re.escape(key)}\s*\|\s*(\d+)\s*\|", spec, re.MULTILINE)
        if rows != [str(value)]:
            issues.append(f"{key}: spec={rows}, code={value}")
    return {"ok": not issues, "values": actual, "issues": issues}


def prose_and_fences(content: str) -> tuple[str, bool]:
    lines, opened = [], None
    for line in content.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            fence = marker.group(1)
            if opened is None:
                opened = fence
            elif (
                fence[0] == opened[0] and len(fence) >= len(opened) and not marker.group(2).strip()
            ):
                opened = None
        elif opened is None:
            lines.append(line)
    return "\n".join(lines), opened is None


def check_docs(root: Path) -> dict:
    """检查现行入口/变更单的围栏和内联相对链接，不做语义或锚点证明。"""
    paths = [root / name for name in ENTRY_DOCS]
    paths.extend(sorted((root / "docs/changes").glob("*.md")))
    issues, links = [], 0
    for path in paths:
        if not path.is_file():
            issues.append(f"missing_document:{path.relative_to(root)}")
            continue
        content, balanced = prose_and_fences(path.read_text(encoding="utf-8"))
        if not balanced:
            issues.append(f"unclosed_fence:{path.relative_to(root)}")
        content = re.sub(r"`[^`\n]+`", "", content)
        for link in re.findall(r"\]\(([^\s)]+)\)", content):
            if link.startswith(("#", "/")) or re.match(r"[\w+.-]+:", link):
                continue
            target = (path.parent / link.split("#", 1)[0]).resolve()
            if not target.is_relative_to(root):
                issues.append(f"link_outside_project:{path.relative_to(root)}:{link}")
            elif not target.exists():
                issues.append(f"missing_link:{path.relative_to(root)}:{link}")
            links += 1
    return {"ok": not issues, "documents": len(paths), "local_links": links, "issues": issues}


def check_archives(root: Path) -> dict:
    issues, count = [], 0
    for manifest in (root / "docs/history").glob("*/manifest.json"):
        for record in json.loads(manifest.read_text(encoding="utf-8"))["files"]:
            path = (root / record["archive_path"]).resolve()
            if (
                not path.is_relative_to(manifest.parent.resolve())
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != record["archive_sha256"]
            ):
                issues.append(f"archive_changed:{record['archive_path']}")
            count += 1
    return {"ok": not issues, "files": count, "issues": issues}


def check_environment() -> dict[str, str]:
    # 子进程只继承本地运行必需项，不继承模型/观测凭据或自动加载环境配置。
    names = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "COMSPEC")
    env = {key: os.environ[key] for key in names if key in os.environ}
    env.update({"WONDER_AGENT_RUNTIME": "legacy", "PYTHONUTF8": "1"})
    return env


def run_step(root: Path, output: Path, name: str, command: list[str], env: dict) -> dict:
    started = time.monotonic()
    log = output / f"{name}.log"
    print(f"[dev-check] {name}", file=sys.stderr, flush=True)
    with log.open("w", encoding="utf-8") as stream:
        try:
            result = subprocess.run(
                command,
                cwd=root,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=180,
                check=False,
            )
            returncode = result.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            stream.write(f"{type(error).__name__}: local check could not complete\n")
            returncode = 1
    return {
        "name": name,
        "ok": returncode == 0,
        "returncode": returncode,
        "command": command,
        "seconds": round(time.monotonic() - started, 3),
        "log": str(log.relative_to(root)),
    }


def run_checks(root: Path, args: argparse.Namespace) -> dict:
    data = (root / "data/dev-checks").resolve()
    if not data.is_relative_to(root):
        raise ValueError("检查报告必须位于项目data内")
    output = data / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8])
    output.mkdir(parents=True)
    checks = []
    for name, check in (
        ("documents", check_docs),
        ("contract_defaults", check_contract),
        ("history_integrity", check_archives),
    ):
        try:
            checks.append({"name": name, **check(root)})
        except (OSError, ValueError, SyntaxError, StopIteration, AttributeError) as error:
            checks.append(
                {
                    "name": name,
                    "ok": False,
                    "issues": [f"{type(error).__name__}: local input needs inspection"],
                }
            )
    env = check_environment()
    commands = [
        ("diff_whitespace", ["git", "diff", "--check"], env),
        ("ruff", [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"], env),
    ]
    if args.full or args.tests:
        commands.append(("pytest", [sys.executable, "-m", "pytest", "-q", *args.tests], env))
    if args.pi:
        commands.append(
            (
                "pytest_pi",
                [sys.executable, "-m", "pytest", "-q"],
                {**env, "WONDER_AGENT_RUNTIME": "pi"},
            )
        )
    if args.node:
        prefix = ["npm", "--prefix", "src/wonder_stockresearch/pi_runtime", "run"]
        commands.extend(
            (name, [*prefix, script], env)
            for name, script in (("pi_types", "typecheck"), ("pi_generated", "check:generated"))
        )
    for name, command, child_env in commands:
        checks.append(run_step(root, output, name, command, child_env))
    report = {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "report": str((output / "report.json").relative_to(root)),
        "limits": "仅列出的本地检查；未运行真实评测、研究命令、语义审查或发布",
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("context", help="只读列出现行入口、源码默认值与工作区状态；不读业务库")
    check = sub.add_parser("check", help="检查现行文档、10项默认值、历史哈希、diff和Ruff")
    check.add_argument("--tests", nargs="+", default=[], help="定向pytest节点，限tests/下")
    check.add_argument("--full", action="store_true", help="运行完整离线pytest")
    check.add_argument("--pi", action="store_true", help="在--full之外再运行Pi默认路径pytest")
    check.add_argument("--node", action="store_true", help="现有本地npm类型/生成文件检查，不安装")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.command == "context":
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            env=check_environment(),
        )
        report = {
            "ok": result.returncode == 0,
            "read_in_order": [
                "AGENTS.md",
                "README.md",
                "PLAN.md",
                "docs/research-spec.md",
                "docs/technical-spec.md",
                "docs/teaching-contract.md",
                "PROGRESS.md",
            ],
            "current_defaults": source_contract(root),
            "git_status": result.stdout,
            "changes": sorted(
                str(p.relative_to(root)) for p in (root / "docs/changes").glob("*.md")
            ),
            "next": "读PLAN与对应变更单；不自动授权真实调用",
        }
    else:
        if args.pi and not args.full:
            parser.error("--pi必须与--full一起使用")
        if args.full and args.tests:
            parser.error("--full与--tests择一使用")
        for node in args.tests:
            path = (root / node.split("::", 1)[0]).resolve()
            if (
                not path.is_relative_to((root / "tests").resolve())
                or not path.exists()
                or node.startswith("-")
            ):
                parser.error("定向测试必须是tests/内存在的路径/节点")
        report = run_checks(root, args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
