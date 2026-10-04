"""自然语言入口的受控意图路由；只返回计划，不执行研究副作用。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from . import envfile, progress, skill_registry, storage, writer
from .models import RoutePlan


class RouterError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 1) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


_TICKER_RE = re.compile(r"\b(?:HK\.\d{5}|US\.[A-Z0-9.-]+|SYNTHETIC)\b")
_FIXED_RESEARCH_RE = re.compile(
    r"研究(?:一下|一份|公司)?|写(?:一份|一下)?报告|生成(?:一份|公司)?报告|"
    r"公司研究|标准研究|正式报告|全面研究"
)
ROUTER_PROMPT_VERSION = "natural-router-v2"


def _system_prompt(root: Path) -> str:
    return (
        "你是 wonder-stockresearch 的自然语言路由器。你只负责识别用户意图，"
        "不能执行工具、不能写数据库、不能生成正式研究结论。\n"
        "固定研究：用户要写报告、研究公司、全面分析公司时使用 fixed_research。"
        "开放问题：用户只问风险、业务、年报细节、反方等时使用 interactive_question。"
        "如果无法判断固定研究还是临时分析，使用 clarify。\n"
        "支持自然、口语、不严格的中文表达；不要因为没有出现‘标准模板’就拒绝。"
        "如果用户提到的公司简称可能对应多个上市公司（例如‘保利’），ticker 必须填 null；"
        "不要为了凑字段猜测唯一 ticker。company_name 填用户原始搜索词，后续由证券目录和用户选择消歧。"
        "如果用户给出明确股票代码则照原样填写，并设置 requires_confirmation=true。\n"
        "输出必须是单个 JSON 对象，字段只能是：intent、company_name、ticker、question、focus、"
        "confidence、reason、requires_confirmation、skill_names。skill_names 只列与该请求直接相关且 available=true 的名称；"
        "没有合适 Skill 时返回空数组。\n"
        f"当前可用 Skill 摘要：\n{skill_registry.catalog_prompt(root)}"
    )


def _fallback_ticker(text: str) -> str | None:
    match = _TICKER_RE.search(text.upper())
    return match.group(0) if match else None


def _watchlist_ticker(root: Path, company_name: str | None) -> str | None:
    if not company_name:
        return None
    target = company_name.casefold().strip()
    storage.initialize(root)
    with storage.open_db(root) as connection:
        candidates = []
        for stock in storage.list_stocks(connection, watched_only=False):
            name = str(stock.get("name") or "").casefold().strip()
            if name and (name == target or name in target or target in name):
                candidates.append(str(stock["ticker"]))
    return candidates[0] if len(set(candidates)) == 1 else None


def _looks_like_fixed_research(text: str) -> bool:
    """固定入口词是产品命令语义，不能完全交给模型自由解释。"""
    return bool(_FIXED_RESEARCH_RE.search(text.strip()))


def _parse_plan(content: str) -> RoutePlan:
    try:
        payload = json.loads(content)
        plan = RoutePlan.model_validate(payload)
    except json.JSONDecodeError as error:
        raise RouterError("invalid_route", f"DeepSeek 路由结果不是合法 JSON：{error.msg}", exit_code=1) from error
    except ValidationError as error:
        details = "；".join(
            f"{'.'.join(str(part) for part in item.get('loc', ()))}：{item.get('msg', '')}"
            for item in error.errors()[:3]
        )
        raise RouterError(
            "invalid_route",
            f"DeepSeek 路由结果不符合协议{f'：{details}' if details else ''}",
            exit_code=1,
        ) from error
    return plan


def _repair_plan(
    root: Path,
    text: str,
    invalid_content: str,
    error: RouterError,
    *,
    model: str,
) -> RoutePlan:
    """对协议错误做一次有限修复，不执行任何业务副作用。"""
    repair_prompt = (
        "你是 JSON 协议修复器。请根据原始用户输入和上一次模型输出，"
        "只返回符合给定字段的单个 JSON 对象，不要解释。"
        "字段必须是 intent、company_name、ticker、question、focus、confidence、reason、"
        "requires_confirmation、skill_names；简称有歧义时 ticker 必须为 null，focus 使用 general。\n"
        f"当前协议错误：{error.message}\n"
        "允许的 intent：fixed_research、interactive_question、memo_save、session_followup、"
        f"status_query、clarify、unsupported。\n项目 Skill 摘要：\n{skill_registry.catalog_prompt(root)}"
    )
    response = writer._request_deepseek(
        api_key=writer._api_key(),
        model=model,
        messages=[
            {"role": "system", "content": repair_prompt},
            {
                "role": "user",
                "content": f"原始输入：{text}\n上一次输出：{invalid_content[:4000]}",
            },
        ],
        temperature=0.0,
        thinking="disabled",
    )
    return _parse_plan(writer._extract_content(response))


def route(
    root: Path,
    text: str,
    *,
    model: str | None = None,
    on_progress: progress.ProgressCallback | None = None,
) -> RoutePlan:
    """调用一次 DeepSeek 识别意图；不执行任何研究命令。"""

    if not text.strip():
        raise RouterError("empty_input", "自然语言输入不能为空", exit_code=2)
    envfile.load_env_file(root / ".env")
    resolved_model = writer._resolved_model(model)
    progress.emit(on_progress, "route", "running", "正在理解自然语言请求")
    try:
        response = writer._request_deepseek(
            api_key=writer._api_key(),
            model=resolved_model,
            messages=[
                {"role": "system", "content": _system_prompt(root)},
                {"role": "user", "content": text.strip()},
            ],
            temperature=0.0,
            thinking="disabled",
        )
        content = writer._extract_content(response)
        try:
            plan = _parse_plan(content)
        except RouterError as first_error:
            progress.emit(on_progress, "route", "retrying", "路由 JSON 不符合协议，尝试修复一次")
            try:
                plan = _repair_plan(root, text.strip(), content, first_error, model=resolved_model)
            except (RouterError, writer.WriterError) as repair_error:
                raise RouterError(
                    "route_protocol_error",
                    f"DeepSeek 路由协议修复失败：{repair_error.message}",
                    exit_code=1,
                ) from repair_error
    except writer.WriterError as error:
        raise RouterError(error.code, error.message, exit_code=error.exit_code) from error
    if plan.intent == "clarify" and _looks_like_fixed_research(text):
        plan = plan.model_copy(
            update={
                "intent": "fixed_research",
                "requires_confirmation": True,
                "reason": "用户使用了固定研究表达，进入正式公司研究；标的仍需候选消歧。",
            }
        )
    if plan.ticker is None:
        fallback = _fallback_ticker(text)
        fallback = fallback or _watchlist_ticker(root, plan.company_name)
        if fallback:
            plan = plan.model_copy(update={"ticker": fallback})
    try:
        # 这里仅验证 catalog 元数据与 capability 绑定；正文和 references 必须在执行时才加载。
        intent_capability = {
            "fixed_research": "fixed_research",
            "interactive_question": "interactive_research",
        }.get(plan.intent)
        if intent_capability:
            skill_registry.validate_for_capability(root, plan.skill_names, intent_capability)
        else:
            skill_registry.resolve_selected(root, plan.skill_names)
    except skill_registry.SkillRegistryError as error:
        raise RouterError(error.code, error.message, exit_code=2) from error
    progress.emit(
        on_progress,
        "route",
        "succeeded",
        f"已识别请求类型：{plan.intent}",
    )
    return plan
