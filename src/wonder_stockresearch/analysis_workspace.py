"""开放问题的最小受限临时计算环境。

这里不是通用 Python 或 shell：代码只能处理由本模块导出的只读 JSON 数据，且只能
使用一个很小的 AST 子集。正式 ResearchRun、SQLite 和项目文件不会暴露给子进程。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import resource
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import fixed_quality, storage
from .models import Source

MAX_EXECUTIONS_PER_SESSION = 3
MAX_DATASET_RECORDS = 400
MAX_CODE_CHARS = 12000
MAX_OUTPUT_BYTES = 64 * 1024
TIMEOUT_SECONDS = 10
MEMORY_BYTES = 256 * 1024 * 1024


class AnalysisWorkspaceError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 2) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_models(connection: object, run_id: str | None, ticker: str) -> list[Source]:
    rows = (
        storage.get_run_sources(connection, run_id)
        if run_id
        else storage.list_sources_for_ticker(connection, ticker)
    )
    return [Source.model_validate_json(row["source_json"]) for row in rows]


def archived_financials(root: Path, row: dict[str, Any], query: str, *, limit: int) -> dict[str, Any]:
    ticker = str(row["ticker"])
    run_id = str(row["run_id"]) if row.get("run_id") else None
    with storage.open_db(root) as connection:
        sources = _source_models(connection, run_id, ticker)
    needle = query.casefold().strip()
    records = []
    for item in fixed_quality._numeric_inputs(sources, root):
        if needle and needle not in item.label.casefold() and needle not in item.period.casefold():
            continue
        records.append(
            {
                "label": item.label,
                "value": str(item.value),
                "period": item.period,
                "currency": item.currency,
                "unit": item.unit,
                "source_id": item.source_id,
                "locator": item.locator,
            }
        )
    records.sort(key=lambda item: (str(item["period"]), str(item["label"])), reverse=True)
    return {
        "ticker": ticker,
        "run_id": run_id,
        "query": query,
        "records": records[: max(1, min(limit, MAX_DATASET_RECORDS))],
        "truncated": len(records) > limit,
    }


def trusted_calculation(root: Path, row: dict[str, Any], metric: str) -> dict[str, Any]:
    ticker = str(row["ticker"])
    run_id = str(row["run_id"]) if row.get("run_id") else None
    with storage.open_db(root) as connection:
        sources = _source_models(connection, run_id, ticker)
    calculations = {item.metric: item for item in fixed_quality.calculate_trusted_metrics(root, sources)}
    calculation = calculations.get(metric)
    if calculation is None:
        raise AnalysisWorkspaceError("trusted_calculation_unavailable", f"没有可用的可信计算：{metric}")
    return {
        "ticker": ticker,
        "run_id": run_id,
        "calculation": calculation.model_dump(mode="json"),
        "source_refs": [item.model_dump(mode="json") for item in calculation.source_refs],
    }


def create_dataset(root: Path, row: dict[str, Any], query: str, *, limit: int) -> dict[str, Any]:
    session_id = str(row["id"])
    with storage.open_db(root) as connection:
        existing = storage.list_analysis_runs(connection, session_id)
    if len(existing) >= MAX_EXECUTIONS_PER_SESSION:
        raise AnalysisWorkspaceError("analysis_execution_limit", "每个 session 最多创建三次临时分析执行")
    archived = archived_financials(root, row, query, limit=limit)
    records = archived["records"]
    if not records:
        raise AnalysisWorkspaceError("analysis_dataset_empty", "归档资料中没有匹配的数值记录")
    source_refs = [
        {"source_id": item["source_id"], "locator": item["locator"], "relation": "supports"}
        for item in records
    ]
    dataset = {
        "schema_version": 1,
        "ticker": row["ticker"],
        "query": query,
        "records": records,
        "source_refs": source_refs,
    }
    analysis_id = str(uuid4())
    workspace = root / "data" / "tmp" / "analysis" / session_id / analysis_id
    workspace.mkdir(parents=True, exist_ok=False)
    input_path = workspace / "input.json"
    storage.write_atomic(input_path, json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    input_path.chmod(0o444)
    dataset_hash = _canonical_hash(dataset)
    limits = {
        "timeout_seconds": TIMEOUT_SECONDS,
        "memory_bytes": MEMORY_BYTES,
        "max_output_bytes": MAX_OUTPUT_BYTES,
        "max_executions_per_session": MAX_EXECUTIONS_PER_SESSION,
    }
    with storage.project_lock(root), storage.open_db(root) as connection:
        execution_index = len(storage.list_analysis_runs(connection, session_id)) + 1
        storage.insert_analysis_run(
            connection,
            analysis_id=analysis_id,
            session_id=session_id,
            ticker=str(row["ticker"]),
            execution_index=execution_index,
            status="dataset_ready",
            dataset_hash=dataset_hash,
            code_hash=None,
            limits_json=json.dumps(limits, ensure_ascii=False, sort_keys=True),
            source_refs_json=json.dumps(source_refs, ensure_ascii=False),
            workspace_path=storage.relative_path(root, workspace),
            created_at=storage.now_utc(),
        )
    return {
        "analysis_id": analysis_id,
        "dataset_hash": dataset_hash,
        "record_count": len(records),
        "source_refs": source_refs,
    }


_ALLOWED_NODES = {
    ast.Module,
    ast.Assign,
    ast.Expr,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Subscript,
    ast.Slice,
    ast.ListComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.Call,
    ast.keyword,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
}
_ALLOWED_CALLS = {
    "abs", "float", "int", "len", "max", "mean", "median", "min", "round", "sorted", "sum"
}


def validate_code(code: str) -> None:
    if not code.strip() or len(code) > MAX_CODE_CHARS:
        raise AnalysisWorkspaceError("invalid_analysis_code", "临时代码为空或超过长度上限")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as error:
        raise AnalysisWorkspaceError("invalid_analysis_code", f"临时代码语法错误：{error.msg}") from error
    assigned_result = False
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_NODES:
            raise AnalysisWorkspaceError("analysis_code_not_allowed", f"不允许的 Python 语法：{type(node).__name__}")
        if isinstance(node, ast.Attribute):
            raise AnalysisWorkspaceError("analysis_code_not_allowed", "不允许属性访问")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or "__" in node.id):
            raise AnalysisWorkspaceError("analysis_code_not_allowed", "不允许 dunder 名称")
        if isinstance(node, ast.Assign):
            if not all(isinstance(target, ast.Name) for target in node.targets):
                raise AnalysisWorkspaceError("analysis_code_not_allowed", "赋值目标只能是普通变量")
            assigned_result = assigned_result or any(
                isinstance(target, ast.Name) and target.id == "result" for target in node.targets
            )
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS
        ):
            raise AnalysisWorkspaceError("analysis_code_not_allowed", "函数调用不在受控白名单中")
        if isinstance(node, ast.comprehension) and node.is_async:
            raise AnalysisWorkspaceError("analysis_code_not_allowed", "不允许异步推导式")
    if not assigned_result:
        raise AnalysisWorkspaceError("analysis_code_result_missing", "临时代码必须给 result 赋 JSON 兼容值")


_RUNNER = r'''
import ast, json, sys
from statistics import median

payload = json.loads(sys.stdin.read())
code = payload["code"]
data = payload["data"]
def mean(values):
    return sum(values) / len(values) if values else None
scope = {"__builtins__": {}, "data": data, "abs": abs, "float": float, "int": int, "len": len, "max": max,
         "mean": mean, "median": median, "min": min, "round": round,
         "sorted": sorted, "sum": sum}
exec(compile(ast.parse(code, mode="exec"), "<analysis>", "exec"), scope, scope)
if "result" not in scope:
    raise ValueError("result missing")
encoded = json.dumps(scope["result"], ensure_ascii=False, allow_nan=False, default=str)
print(encoded)
'''


def _limit_resources() -> None:
    for limit, values in (
        (resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS + 1)),
        (resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES)),
        (resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES)),
        (resource.RLIMIT_NOFILE, (16, 16)),
    ):
        try:
            resource.setrlimit(limit, values)
        except (OSError, ValueError):
            # macOS 对部分 hard limit 不允许降低；主进程 timeout 与 AST 白名单仍生效。
            continue


def run_code(root: Path, row: dict[str, Any], analysis_id: str, code: str) -> dict[str, Any]:
    validate_code(code)
    session_id = str(row["id"])
    with storage.open_db(root) as connection:
        analysis = storage.get_analysis_run(connection, analysis_id)
    if analysis is None or analysis["session_id"] != session_id or analysis["ticker"] != row["ticker"]:
        raise AnalysisWorkspaceError("unknown_analysis_run", "分析执行不属于当前 session")
    if analysis["status"] != "dataset_ready":
        raise AnalysisWorkspaceError("analysis_not_ready", "该数据集已经执行或不可用")
    workspace = root / str(analysis["workspace_path"])
    input_path = workspace / "input.json"
    if not input_path.is_file():
        raise AnalysisWorkspaceError("analysis_dataset_missing", "分析数据集文件不存在", exit_code=1)
    try:
        dataset = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AnalysisWorkspaceError("analysis_dataset_invalid", "分析数据集无法读取", exit_code=1) from error
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    storage.write_atomic(workspace / "code.py", code)
    (workspace / "code.py").chmod(0o444)
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _RUNNER],
            input=json.dumps({"code": code, "data": dataset["records"]}, ensure_ascii=False),
            text=True,
            capture_output=True,
            cwd=workspace,
            env={"PATH": os.defpath, "LC_ALL": "C"},
            timeout=TIMEOUT_SECONDS,
            preexec_fn=_limit_resources,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        _finish_failed(root, analysis_id, "analysis_timeout", "临时代码超过十秒限制", code_hash)
        raise AnalysisWorkspaceError("analysis_timeout", "临时代码超过十秒限制") from error
    if completed.returncode != 0:
        _finish_failed(root, analysis_id, "analysis_failed", "临时代码被限制或执行失败", code_hash)
        raise AnalysisWorkspaceError("analysis_failed", "临时代码被限制或执行失败")
    if len(completed.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        _finish_failed(root, analysis_id, "analysis_output_too_large", "临时结果超过 64 KB", code_hash)
        raise AnalysisWorkspaceError("analysis_output_too_large", "临时结果超过 64 KB")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        _finish_failed(root, analysis_id, "analysis_result_invalid", "临时代码没有返回 JSON", code_hash)
        raise AnalysisWorkspaceError("analysis_result_invalid", "临时代码没有返回 JSON") from error
    result_path = workspace / "result.json"
    storage.write_atomic(result_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    result_path.chmod(0o444)
    summary = json.dumps(result, ensure_ascii=False, default=str)[:2000]
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.finish_analysis_run(
            connection,
            analysis_id,
            status="succeeded",
            result_json=json.dumps(result, ensure_ascii=False, default=str),
            result_summary=summary,
            finished_at=storage.now_utc(),
        )
        connection.execute("UPDATE analysis_runs SET code_hash = ? WHERE id = ?", (code_hash, analysis_id))
    return {
        "analysis_id": analysis_id,
        "status": "succeeded",
        "result": result,
        "source_refs": json.loads(str(analysis["source_refs_json"])),
    }


def _finish_failed(root: Path, analysis_id: str, status: str, summary: str, code_hash: str) -> None:
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.finish_analysis_run(
            connection,
            analysis_id,
            status=status,
            result_json=None,
            result_summary=summary,
            finished_at=storage.now_utc(),
        )
        connection.execute("UPDATE analysis_runs SET code_hash = ? WHERE id = ?", (code_hash, analysis_id))


def read_result(root: Path, row: dict[str, Any], analysis_id: str) -> dict[str, Any]:
    with storage.open_db(root) as connection:
        analysis = storage.get_analysis_run(connection, analysis_id)
    if analysis is None or analysis["session_id"] != row["id"] or analysis["ticker"] != row["ticker"]:
        raise AnalysisWorkspaceError("unknown_analysis_run", "分析执行不属于当前 session")
    return {
        "analysis_id": analysis_id,
        "status": analysis["status"],
        "dataset_hash": analysis["dataset_hash"],
        "code_hash": analysis["code_hash"],
        "result": json.loads(analysis["result_json"]) if analysis["result_json"] else None,
        "result_summary": analysis["result_summary"],
        "source_refs": json.loads(analysis["source_refs_json"]),
    }
