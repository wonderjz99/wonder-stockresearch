"""CLI：阶段 1 的离线校验/渲染，以及阶段 2 的持久化研究记录。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from . import agent, chat, envfile, progress, research, storage, writer
from .models import (
    DraftReview,
    InteractiveFollowup,
    InteractiveQuestion,
    MemoRequest,
    ResearchPacket,
    SourceRequest,
)
from .reports import render_company
from .research import ResearchError, ValidationIssue, validate_packet
from .router import RouterError
from .sources import SourceError
from .writer import WriterError


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


def _read_draft_review(input_path: str) -> DraftReview:
    try:
        raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
        return DraftReview.model_validate_json(raw)
    except OSError as error:
        raise CommandError("input_read_error", f"无法读取审查 JSON：{error}", 1) from error
    except ValidationError as error:
        raise CommandError("invalid_review", f"输入不符合 DraftReview：{error}", 2) from error


def _read_interactive_question(input_path: str) -> InteractiveQuestion:
    try:
        raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
    except OSError as error:
        raise CommandError("input_read_error", f"无法读取 JSON 输入：{error}", 1) from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CommandError("invalid_json", f"JSON 格式错误：{error.msg}", 2) from error
    try:
        return InteractiveQuestion.model_validate(payload)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}：{item['msg']}" for item in error.errors()
        )
        raise CommandError("invalid_interactive_question", f"输入不符合 InteractiveQuestion：{details}", 2) from error


def _read_interactive_followup(input_path: str) -> InteractiveFollowup:
    try:
        raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
    except OSError as error:
        raise CommandError("input_read_error", f"无法读取 JSON 输入：{error}", 1) from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CommandError("invalid_json", f"JSON 格式错误：{error.msg}", 2) from error
    try:
        return InteractiveFollowup.model_validate(payload)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}：{item['msg']}" for item in error.errors()
        )
        raise CommandError("invalid_interactive_followup", f"输入不符合 InteractiveFollowup：{details}", 2) from error


def _read_memo_request(input_path: str) -> MemoRequest:
    try:
        raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
    except OSError as error:
        raise CommandError("input_read_error", f"无法读取 JSON 输入：{error}", 1) from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CommandError("invalid_json", f"JSON 格式错误：{error.msg}", 2) from error
    try:
        return MemoRequest.model_validate(payload)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}：{item['msg']}" for item in error.errors()
        )
        raise CommandError("invalid_memo_request", f"输入不符合 MemoRequest：{details}", 2) from error


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
    draft_read = subparsers.add_parser("draft-read", help="只读输出当前保存草稿 JSON")
    draft_read.add_argument("run_id")
    review_read = subparsers.add_parser("review-read", help="只读输出最新结构化审查 JSON")
    review_read.add_argument("run_id")
    draft_preview = subparsers.add_parser("draft-preview", help="生成未定稿 Markdown 预览")
    draft_preview.add_argument("run_id")
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
    collect.add_argument(
        "--kind", choices=["all", "market", "fundamentals", "filings"], default="all"
    )
    collect.add_argument("--quiet", action="store_true", help="关闭简短进度输出")
    compare = subparsers.add_parser("compare", help="与 baseline 比较结构化差异")
    compare.add_argument("run_id")
    backup = subparsers.add_parser("backup", help="备份数据库、来源与完成报告")
    backup.add_argument("--output", required=True, help="root/data/backups/ 下的新目录")
    write_draft = subparsers.add_parser("write-draft", help="调用 DeepSeek 生成草稿（不自动 finish）")
    write_draft.add_argument("run_id")
    write_draft.add_argument("--strategy", choices=["staged", "one-shot"], default="staged")
    write_draft.add_argument("--provider", choices=["deepseek"], default="deepseek")
    write_draft.add_argument("--model", default=None, help="模型名；默认 deepseek-flash 或 WONDER_DEEPSEEK_MODEL")
    write_draft.add_argument(
        "--temperature", type=float, default=None, help="采样温度 0–2；默认 0.2 或 WONDER_DEEPSEEK_TEMPERATURE"
    )
    write_draft.add_argument(
        "--thinking",
        choices=["enabled", "disabled"],
        default=None,
        help="thinking 模式；默认 disabled（temperature 仅在 disabled 时生效）",
    )
    write_draft.add_argument("--quiet", action="store_true", help="关闭简短进度输出")
    review_draft = subparsers.add_parser("review-draft", help="审查草稿或导入人工审查结果")
    review_draft.add_argument("run_id")
    review_draft.add_argument("--provider", choices=["deepseek"], default="deepseek")
    review_draft.add_argument("--model", default=None)
    review_draft.add_argument("--input", default=None, help="DraftReview JSON；- 代表 stdin")

    ask = subparsers.add_parser("ask", help="回答公司临时问题，不修改正式报告")
    ask.add_argument("ticker")
    ask.add_argument("--input", required=True, help="InteractiveQuestion JSON；- 代表 stdin")
    ask.add_argument("--model", default=None, help="模型名；默认 WONDER_DEEPSEEK_MODEL 或 deepseek-flash")
    ask.add_argument("--quiet", action="store_true", help="关闭简短进度输出")
    followup = subparsers.add_parser("ask-followup", help="继续交互 Agent session")
    followup.add_argument("session_id")
    followup.add_argument("--input", required=True, help="InteractiveFollowup JSON；- 代表 stdin")
    followup.add_argument("--quiet", action="store_true", help="关闭简短进度输出")
    status_agent = subparsers.add_parser("ask-status", help="查看交互 Agent session")
    status_agent.add_argument("session_id")
    memo_save = subparsers.add_parser("memo-save", help="把交互结果明确保存为公司 Memo")
    memo_save.add_argument("session_id")
    memo_save.add_argument("--input", required=True, help="MemoRequest JSON；- 代表 stdin")
    memo_list = subparsers.add_parser("memo-list", help="列出公司 Memo")
    memo_list.add_argument("ticker")
    memo_read = subparsers.add_parser("memo-read", help="读取公司 Memo")
    memo_read.add_argument("memo_id")
    chat_cmd = subparsers.add_parser("chat", help="独立自然语言入口")
    chat_cmd.add_argument(
        "--text",
        default=None,
        help="首次自然语言输入；终端中会继续追问，管道/重定向时输出一次 JSON",
    )
    chat_cmd.add_argument("--session-id", default=None, help="恢复已有自然语言 session")
    chat_cmd.add_argument("--model", default=None, help="模型名；默认 WONDER_DEEPSEEK_MODEL 或 deepseek-flash")
    chat_cmd.add_argument("--quiet", action="store_true", help="关闭简短进度输出")
    return parser


def _progress_for(args: argparse.Namespace) -> progress.ProgressCallback | None:
    return None if getattr(args, "quiet", False) else progress.StderrProgress()


def _finish_summary(root: Path, result: dict[str, object]) -> str:
    """Create a concise human-readable summary without exposing the raw result JSON."""
    run_id = str(result.get("run_id") or "")
    score = result.get("score_total")
    quality = result.get("quality_status")
    if (score is None or quality is None) and run_id:
        try:
            current = research.get_status(root, run_id)
        except (ResearchError, storage.StorageError, OSError):
            current = {}
        if score is None:
            score = current.get("score_total")
        if quality is None:
            quality = current.get("quality_status")

    run_dir_value = result.get("run_dir")
    run_dir = Path(str(run_dir_value)) if run_dir_value else Path()
    if not run_dir.is_absolute():
        run_dir = root / run_dir
    run_dir = run_dir.resolve()
    quality_text = {
        "complete": "完整",
        "evidence_incomplete": "证据不完整",
        "draft": "草稿",
    }.get(str(quality), "未知" if quality is None else str(quality))
    score_text = f"{score} / 60" if score is not None else "未评估 / 60"
    lines = [
        "正式报告已完成并冻结",
        f"状态：{'已完成' if result.get('status') == 'completed' else result.get('status', '未知')}",
        f"质量：{quality_text}",
        f"护城河总分：{score_text}",
        f"运行目录：{run_dir}",
        f"报告：{run_dir / 'company.md'}",
        f"结构化数据：{run_dir / 'research.json'}",
    ]
    review_path = run_dir / "review.md"
    artifacts = result.get("artifacts")
    review_recorded = isinstance(artifacts, dict) and "review.md" in artifacts
    if review_path.is_file() or review_recorded:
        lines.append(f"复评报告：{review_path}")
    return "\n".join(lines)


def _print_finish_summary(root: Path, result: dict[str, object]) -> None:
    print(_finish_summary(root, result))


def _print_chat_response(root: Path, data: dict[str, object]) -> None:
    status = data.get("status")
    if status == "validation_failed":
        message = str(
            data.get("message")
            or "正式草稿未通过证据校验；正式报告没有生成或冻结。"
        )
        print(message)
        if data.get("run_id"):
            print(f"run_id: {data['run_id']}")
        issues = data.get("issues")
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict):
                    location = issue.get("location", "校验")
                    code = issue.get("code", "validation_error")
                    detail = issue.get("message", "未提供详情")
                    print(f"- {location} [{code}]：{detail}")
        if data.get("session_id"):
            print(f"session_id: {data['session_id']}")
        return
    if status == "completed" and isinstance(data.get("result"), dict):
        result = data["result"]
        if isinstance(result.get("report"), dict):
            print(result["report"].get("answer", ""))
        else:
            if sys.stdout.isatty():
                _print_finish_summary(root, result)
            else:
                _emit(True, data, [])
            return
    elif data.get("message"):
        print(str(data["message"]))
    candidates = data.get("candidates")
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            marker = "（模型建议，未验证）" if not item.get("verified", False) else ""
            print(
                f"{item.get('candidate_id', '?')}. {item.get('ticker', '')} "
                f"{item.get('name', '')} [{item.get('market', '')}]{marker}"
            )
    if data.get("session_id"):
        print(f"session_id: {data['session_id']}")


def _chat_needs_followup(data: dict[str, object]) -> bool:
    """判断人类终端是否应继续复用当前 chat session。"""
    status = data.get("status")
    if status in {
        "awaiting_symbol_selection",
        "awaiting_confirmation",
        "awaiting_finish",
        "waiting_user",
    }:
        return True
    if status == "completed" and isinstance(data.get("result"), dict):
        # 交互 Agent 的 completed 结果可以继续追问；正式报告完成后直接退出。
        return isinstance(data["result"].get("report"), dict)
    return False


def _run_chat_loop(
    root: Path,
    args: argparse.Namespace,
    *,
    session_id: str | None,
    first_data: dict[str, object] | None = None,
) -> int:
    if first_data is not None:
        _print_chat_response(root, first_data)
    while True:
        try:
            text = input("wonder> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.casefold() in {"exit", "quit", "退出"}:
            return 0
        data = chat.run_once(root, text, session_id=session_id, model=args.model, on_progress=_progress_for(args))
        session_id = str(data.get("session_id") or session_id or "") or None
        _print_chat_response(root, data)
        if not _chat_needs_followup(data):
            return 2 if data.get("status") == "validation_failed" else 0


def _run_chat(root: Path, args: argparse.Namespace) -> int:
    callback = _progress_for(args)
    if args.text is not None:
        data = chat.run_once(
            root,
            args.text,
            session_id=args.session_id,
            model=args.model,
            on_progress=callback,
        )
        # `--text` 可作为脚本的一次性 JSON 入口；stdin/stdout 都是终端时，
        # 直接接入后续确认/追问，不要求复制 session_id 再启动一遍命令。
        if sys.stdin.isatty() and sys.stdout.isatty() and _chat_needs_followup(data):
            return _run_chat_loop(
                root,
                args,
                session_id=str(data.get("session_id") or "") or None,
                first_data=data,
            )
        if (
            sys.stdout.isatty()
            and data.get("status") == "completed"
            and isinstance(data.get("result"), dict)
            and not isinstance(data["result"].get("report"), dict)
        ):
            _print_finish_summary(root, data["result"])
            return 0
        if sys.stdout.isatty() and data.get("status") == "validation_failed":
            _print_chat_response(root, data)
            return 2
        if data.get("status") == "validation_failed":
            raw_issues = data.get("issues")
            issues = [item for item in raw_issues if isinstance(item, dict)] if isinstance(raw_issues, list) else []
            _emit(False, data, issues)
            return 2
        _emit(True, data, [])
        return 0
    print("wonder-research chat；输入 exit 或 quit 退出。")
    return _run_chat_loop(root, args, session_id=args.session_id)


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
    if args.command == "draft-read":
        _emit(True, research.get_draft(root, args.run_id), [])
        return 0
    if args.command == "review-read":
        _emit(True, research.get_draft_review(root, args.run_id), [])
        return 0
    if args.command == "draft-preview":
        _emit(True, research.preview_draft(root, args.run_id), [])
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
        result = research.finish_run(root, args.run_id)
        if sys.stdout.isatty():
            _print_finish_summary(root, result)
        else:
            _emit(True, result, [])
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
        data, issues = research.collect(
            root,
            args.run_id,
            args.kind,
            on_progress=_progress_for(args),
        )
        succeeded = int(data.get("succeeded", 0)) > 0
        _emit(succeeded, data, _validation_payload(issues))
        return 0 if succeeded else 1
    if args.command == "compare":
        _emit(True, research.compare_runs(root, args.run_id), [])
        return 0
    if args.command == "backup":
        _emit(True, research.backup(root, args.output), [])
        return 0
    if args.command == "write-draft":
        data, issues = writer.write_draft(
            root,
            args.run_id,
            provider=args.provider,
            model=args.model,
            temperature=args.temperature,
            thinking=args.thinking,
            strategy=args.strategy,
            on_progress=_progress_for(args),
        )
        ok = not issues
        _emit(ok, data, _validation_payload(issues))
        return 0 if ok else 2
    if args.command == "review-draft":
        imported = _read_draft_review(args.input) if args.input else None
        data, issues = writer.review_draft(
            root, args.run_id, provider=args.provider, model=args.model,
            review=imported, reviewer_type="human" if imported else "model",
        )
        _emit(not issues, data, _validation_payload(issues))
        return 0 if not issues else 2
    if args.command == "ask":
        question = _read_interactive_question(args.input)
        data = agent.ask(root, args.ticker, question, model=args.model, on_progress=_progress_for(args))
        _emit(True, data, [])
        return 0
    if args.command == "ask-followup":
        request = _read_interactive_followup(args.input)
        data = agent.followup(root, args.session_id, request, on_progress=_progress_for(args))
        _emit(True, data, [])
        return 0
    if args.command == "ask-status":
        _emit(True, agent.get_status(root, args.session_id), [])
        return 0
    if args.command == "memo-save":
        request = _read_memo_request(args.input)
        _emit(True, agent.memo_save(root, args.session_id, request), [])
        return 0
    if args.command == "memo-list":
        _emit(True, agent.memo_list(root, args.ticker), [])
        return 0
    if args.command == "memo-read":
        _emit(True, agent.memo_read(root, args.memo_id), [])
        return 0
    if args.command == "chat":
        return _run_chat(root, args)
    raise CommandError("unknown_command", f"未实现的命令：{args.command}", 2)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = _project_root(args.root)
        envfile.load_env_file(root / ".env")
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
    except storage.StorageError as error:
        _emit(False, {}, [_error_issue(error.code, "storage", error.message)])
        return 1
    except WriterError as error:
        _emit(False, {}, [_error_issue(error.code, "writer", error.message)])
        return error.exit_code
    except agent.AgentError as error:
        _emit(False, {}, [_error_issue(error.code, "agent", error.message)])
        return error.exit_code
    except chat.ChatError as error:
        _emit(False, {}, [_error_issue(error.code, "chat", error.message)])
        return error.exit_code
    except RouterError as error:
        _emit(False, {}, [_error_issue(error.code, "router", error.message)])
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
