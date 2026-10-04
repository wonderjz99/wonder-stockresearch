"""加载项目根 `.env`（KEY=VALUE）到进程环境。

只用于本地运行便利：不覆盖已存在的环境变量，不打印或记录文件内容。
`.env` 已被 .gitignore 忽略，绝不提交；示例见 `.env.example`。
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path) -> int:
    """把 `.env` 中的变量写入 os.environ（已存在的不覆盖）；返回新增条数。"""
    if not path.is_file():
        return 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    loaded = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        key, _, raw_value = stripped.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if key in os.environ:
            continue
        os.environ[key] = _parse_value(raw_value)
        loaded += 1
    return loaded
