"""CLI：阶段 1 的离线校验/渲染，以及阶段 2 的持久化研究记录。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from . import research
from .models import ResearchPacket, SourceRequest
from .reports import render_company
from .research import ResearchError, ValidationIssue, validate_packet
from .sources import SourceError


class CommandError(Exception):
    def __init__(self, code: str, message: str, exit_code: int) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


def _emit(ok: bool, data: dict[str, object], issues: list[dict[str, str]]) -> None:
    print(json.dumps({"ok": ok, "data": data, "issues": issues}, ensure_ascii=False, default=str))


def _error_issue(code: str, location: str, message: str) -> dict[str, str]:
    return {"code": code, "location": location, "message": message, "severity": "error"}


def _project_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir() or not (root / "pyproject.toml").is_file():
        raise CommandError("invalid_root", "--root 必须是含 pyproject.toml 的项目根目录", 1)
    return root


def _read_packet(input_path: str) -> ResearchPacket:
    try:
        raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
    except OSError as error:
        raise CommandError("input_read_error", f"无法读取 JSON 输入：{error}", 1) from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CommandError("invalid_json", f"JSON 格式错误：{error.msg}", 2) from error
    try:
        return ResearchPacket.model_validate(payload)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}：{item['msg']}" for item in error.errors()
        )
        raise CommandError("invalid_packet", f"输入不符合 ResearchPacket：{details}", 2) from error


def _read_source_request(input_path: str) -> SourceRequest:
    try:
        raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
    except OSError as error:
        raise CommandError("input_read_error", f"无法读取 JSON 输入：{error}", 1) from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CommandError("invalid_json", f"JSON 格式错误：{error.msg}", 2) from error
    try:
        return SourceRequest.model_validate(payload)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}：{item['msg']}" for item in error.errors()
        )
        raise CommandError("invalid_source_request", f"输入不符合 SourceRequest：{details}", 2) from error


def _validation_payload(issues: list[ValidationIssue]) -> list[dict[str, str]]:
    return [issue.to_dict() for issue in issues]


def _render_output_path(root: Path, output: str) -> Path:
    data_root = (root / "data").resolve()
    requested = Path(output)
    destination = (root / requested).resolve() if not requested.is_absolute() else requested.resolve()
    if not destination.is_relative_to(data_root):
        raise CommandError("invalid_output", "--output 必须位于项目 root/data/ 下", 2)
    return destination


def _parse_as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CommandError("invalid_as_of", f"--as-of 不是有效 ISO 时间：{value}", 2) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CommandError("invalid_as_of", "--as-of 必须带时区", 2)
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wonder-research")
    parser.add_argument("--root", default=".", help="项目根目录；默认当前目录")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-packet", help="校验离线研究包")
    validate.add_argument("--input", required=True, help="JSON 文件路径；- 代表 stdin")
    render = subparsers.add_parser("render-packet", help="校验并渲染离线研究包")
    render.add_argument("--input", required=True, help="JSON 文件路径；- 代表 stdin")
    render.add_argument("--output", required=True, help="root/data/ 下的 Markdown 输出路径")

    watch = subparsers.add_parser("watch", help="维护本地股票清单")
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)
    watch_add = watch_sub.add_parser("add", help="加入清单")
    watch_add.add_argument("ticker")
    watch_add.add_argument("--name", default=None)
    watch_remove = watch_sub.add_parser("remove", help="移出清单，保留历史")
    watch_remove.add_argument("ticker")
    watch_sub.add_parser("list", help="列出清单")

    start = subparsers.add_parser("start", help="开始一次研究 run")
    start.add_argument("ticker")
    start.add_argument("--as-of", default=None, help="带时区的 ISO 时间；默认当前时间")
    start.add_argument("--baseline", default=None, help="同股票已完成的基准 run_id")
    status = subparsers.add_parser("status", help="查看 run 状态")
    status.add_argument("run_id")
    history = subparsers.add_parser("history", help="查看某股票的研究历史")
    history.add_argument("ticker")
    save_draft = subparsers.add_parser("save-draft", help="保存草稿与来源")
    save_draft.add_argument("run_id")
    save_draft.add_argument("--input", required=True, help="ResearchPacket JSON；- 代表 stdin")
    validate_run = subparsers.add_parser("validate", help="校验已保存草稿")
    validate_run.add_argument("run_id")
    finish = subparsers.add_parser("finish", help="校验后生成正式报告")
    finish.add_argument("run_id")
    resume = subparsers.add_parser("resume", help="把 failed 恢复到 draft")
    resume.add_argument("run_id")

    source_add = subparsers.add_parser("source-add", help="导入 URL 或本地文件并归档")
    source_add.add_argument("run_id")
    source_add.add_argument("--input", required=True, help="SourceRequest JSON；- 代表 stdin")
    sources_cmd = subparsers.add_parser("sources", help="列出 run 的来源")
    sources_cmd.add_argument("run_id")
    source_read = subparsers.add_parser("source-read", help="读取来源片段")
    source_read.add_argument("run_id")
    source_read.add_argument("source_id")
    source_read.add_argument("--page", type=int, default=None, help="PDF 页码（从 1 起）")
    source_read.add_argument("--start-line", type=int, default=None, help="提取文本起始行")
    source_read.add_argument("--limit-lines", type=int, default=None, help="返回行数，默认 120，上限 300")
    collect = subparsers.add_parser("collect", help="采集行情/财务/公告")
    collect.add_argument("run_id")
    collect.add_argument("--kind", choices=["all", "market", "filings"], default="all")
    return parser


def _run_business(root: Path, args: argparse.Namespace) -> int:
    if args.command == "watch":
        if args.watch_command == "add":
            data = research.watch_add(root, args.ticker, args.name)
        elif args.watch_command == "remove":
            data = research.watch_remove(root, args.ticker)
        else:
            data = research.watch_list(root)
        _emit(True, data, [])
        return 0
    if args.command == "start":
        as_of = _parse_as_of(args.as_of) if args.as_of else None
        data = research.start_run(root, args.ticker, as_of=as_of, baseline_run_id=args.baseline)
        _emit(True, data, [])
        return 0
    if args.command == "status":
        _emit(True, research.get_status(root, args.run_id), [])
        return 0
    if args.command == "history":
        _emit(True, research.get_history(root, args.ticker), [])
        return 0
    if args.command == "save-draft":
        packet = _read_packet(args.input)
        data, issues = research.save_draft(root, args.run_id, packet)
        _emit(True, data, _validation_payload(issues))
        return 0
    if args.command == "validate":
        issues = research.validate_run(root, args.run_id)
        if issues:
            _emit(False, {"valid": False}, _validation_payload(issues))
            return 2
        _emit(True, {"valid": True}, [])
        return 0
    if args.command == "finish":
        _emit(True, research.finish_run(root, args.run_id), [])
        return 0
    if args.command == "resume":
        _emit(True, research.resume_run(root, args.run_id), [])
        return 0
    if args.command == "source-add":
        request = _read_source_request(args.input)
        _emit(True, research.source_add(root, args.run_id, request), [])
        return 0
    if args.command == "sources":
        _emit(True, research.list_sources(root, args.run_id), [])
        return 0
    if args.command == "source-read":
        if args.page is not None and args.start_line is not None:
            raise CommandError("invalid_locator", "--page 与 --start-line 不能同时使用", 2)
        data = research.read_source(
            root,
            args.run_id,
            args.source_id,
            page=args.page,
            start_line=args.start_line,
            limit_lines=args.limit_lines,
        )
        _emit(True, data, [])
        return 0
    if args.command == "collect":
        data, issues = research.collect(root, args.run_id, args.kind)
        succeeded = int(data.get("succeeded", 0)) > 0
        _emit(succeeded, data, _validation_payload(issues))
        return 0 if succeeded else 1
    raise CommandError("unknown_command", f"未实现的命令：{args.command}", 2)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = _project_root(args.root)
        if args.command == "validate-packet":
            packet = _read_packet(args.input)
            issues = validate_packet(packet)
            if issues:
                _emit(False, {"valid": False}, _validation_payload(issues))
                return 2
            _emit(True, {"valid": True}, [])
            return 0
        if args.command == "render-packet":
            packet = _read_packet(args.input)
            issues = validate_packet(packet)
            if issues:
                _emit(False, {"valid": False}, _validation_payload(issues))
                return 2
            destination = _render_output_path(root, args.output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(render_company(packet), encoding="utf-8")
            _emit(True, {"output": destination.relative_to(root).as_posix()}, [])
            return 0
        return _run_business(root, args)
    except CommandError as error:
        _emit(False, {}, [_error_issue(error.code, "command", error.message)])
        return error.exit_code
    except SourceError as error:
        _emit(False, {}, [_error_issue(error.code, "source", error.message)])
        return error.exit_code
    except ResearchError as error:
        issues = _validation_payload(error.issues) or [
            _error_issue(error.code, "command", error.message)
        ]
        _emit(False, {}, issues)
        return error.exit_code
