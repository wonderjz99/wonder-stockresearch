"""小型任务记录与已知时间歧义检查；不试图用正则理解所有自然语言。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from wonder_stockresearch import skill_registry, storage
from wonder_stockresearch.interactive.agent_errors import AgentError

SKILL_NAME = "question-clarification"
_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_RELATIVE = re.compile(
    r"(?:最近|近|过去|last|past)\s*(\d+|[一二两三四五六七八九十])\s*(?:个)?\s*(?:完整财年|财年|自然年|年|fiscal years?|calendar years?|years?)",
    re.IGNORECASE,
)
_YEAR = re.compile(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)")
_RANGE = re.compile(r"((?:19|20)\d{2})\s*(?:年)?\s*(?:至|到|—|–|-|~|～)\s*((?:19|20)\d{2})")
_QUARTER = re.compile(r"((?:19|20)\d{2})\s*(?:年|/|-)?\s*Q([1-4])", re.IGNORECASE)


def load_method(root: Path):
    """项目内版本优先；安装 wheel 后使用同一源文件打包的内置方法。"""
    if (root / "skills" / SKILL_NAME / "SKILL.md").is_file():
        origin = root
    else:
        package = Path(__file__).resolve().parents[1]
        origin = package / "resources"
        if not (origin / "skills" / SKILL_NAME / "SKILL.md").is_file():
            origin = package.parents[1]  # 源码开发时的项目根目录。
    try:
        return skill_registry.load_for_capability(origin, [SKILL_NAME], "interactive_research")
    except skill_registry.SkillRegistryError as error:
        raise AgentError(error.code, error.message, exit_code=2) from error


def freeze_clock(now: datetime) -> dict[str, str]:
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    return {
        "as_of": local.isoformat(),
        "date": local.date().isoformat(),
        "timezone": "Asia/Shanghai",
    }


def explicit_years(text: str) -> list[int]:
    years = {int(item) for item in _YEAR.findall(text)}
    for first, last in _RANGE.findall(text):
        start, end = int(first), int(last)
        if 0 <= end - start <= 30:
            years.update(range(start, end + 1))
    return sorted(years)


def resolve(text: str, clock: dict[str, str], *, confirmed: dict | None = None) -> dict[str, Any]:
    """只处理明确年份和有限相对年语法；其他语义仍交给同一 Agent/Skill。"""
    years = explicit_years(text)
    matches = list(_RELATIVE.finditer(text))
    count = None
    if matches:
        raw = matches[-1].group(1)
        count = int(raw) if raw.isdigit() else _CN_NUMBERS[raw]
        if not 1 <= count <= 20:
            raise AgentError("unsupported_period_count", "相对年度数量须在1–20之间", exit_code=2)
    basis_explicit = bool(re.search(r"完整自然年|calendar years?", text, re.IGNORECASE))
    inherited_basis = (confirmed or {}).get("basis")
    if re.search(r"财年|fiscal|滚动|rolling", text, re.IGNORECASE):
        inherited_basis = None
    basis = "calendar_year" if basis_explicit else inherited_basis
    if not years and count:
        end = int(clock["date"][:4]) - 1
        years = list(range(end - count + 1, end + 1))
    unresolved = []
    if count and not explicit_years(text) and not basis:
        unresolved.append("relative_period_basis")
    required_operations = []
    for name, pattern in (
        ("mean", r"平均|均值|\bmean\b|\baverage\b"),
        ("range", r"极差|\brange\b"),
        ("population_stddev", r"总体标准差|population.*?(?:std|standard deviation)"),
        ("changes", r"变化|变动|逐年差|\bchanges?\b"),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            required_operations.append(name)
    if required_operations and not years:
        unresolved.append("period_range")
    from wonder_stockresearch.interactive import agent_company

    metric_terms = [
        list(group)
        for group in agent_company._SEARCH_ALIASES
        if any(term in text.casefold() for term in group)
    ]
    kind = (
        "quarter"
        if re.search(r"季度|单季|quarter|\bQ[1-4]\b", text, re.IGNORECASE) or _QUARTER.search(text)
        else "interim"
        if re.search(r"中期|半年|interim", text, re.IGNORECASE)
        else "annual"
        if years
        else None
    )
    all_quarters = kind == "quarter" and bool(re.search(r"单季度|每季度|各季度|分季度", text))
    target_periods = (
        [f"{year}/Q{quarter}" for year in years for quarter in range(1, 5)] if all_quarters else []
    )
    if not target_periods:
        target_periods = [f"{year}/Q{quarter}" for year, quarter in _QUARTER.findall(text)]
    return {
        "schema_version": 1,
        "target_years": years,
        "period_kind": kind,
        "target_periods": target_periods,
        "all_quarters": all_quarters,
        "required_operations": required_operations,
        "metric_terms": metric_terms,
        "value_attribute": "yoy" if "同比" in text else "qoq" if "环比" in text else "data",
        "relative_count": count,
        "basis": basis or ("explicit" if years and not count else None),
        "unresolved": unresolved,
        "accept_partial": bool(
            re.search(r"缺(?:年|失|的).*?(?:标|说明|缺口)|保留.*?缺口|允许缺失", text)
        ),
        "origin": "user_explicit"
        if explicit_years(text)
        else "user_confirmed_rule"
        if basis
        else "candidate",
        "analysis_clock": clock,
    }


def load(root: Path, row: dict[str, Any], turn_id: str) -> dict[str, Any]:
    with storage.open_db(root) as connection:
        turn = storage.get_agent_turn(connection, turn_id)
        events = storage.list_agent_events(connection, str(row["id"]))
        attempts = storage.list_agent_attempts(connection, turn_id)
    payload = json.loads(turn["question_json"])
    if not payload.get("_quality_version"):
        return {}  # 已有 Turn 的契约不升级。
    clock = payload["_analysis_clock"]
    previous = None
    for event in events:
        if event["event_type"] == "question_requirements":
            saved = json.loads(event["payload_json"])
            if event.get("turn_id") == turn_id:
                previous = saved
            elif (
                (previous is None or "target_years" not in previous)
                and saved.get("origin") == "user_confirmed_rule"
                and saved.get("basis") == "calendar_year"
            ):
                previous = {"basis": saved.get("basis")}
    task = (
        previous
        if previous and "target_years" in previous
        else resolve(payload["question"], clock, confirmed=previous)
    )
    processed = set(task.get("processed_responses", []))
    for attempt in attempts:
        item = json.loads(attempt["input_json"])
        if (
            item.get("kind") != "user_response"
            or not item.get("response")
            or attempt["id"] in processed
        ):
            continue
        processed.add(attempt["id"])
        response = item["response"].strip()
        if explicit_years(response):
            revised = resolve(response, clock)
            task = {
                **task,
                **{k: revised[k] for k in ("target_years", "basis", "unresolved", "origin")},
            }
            task["origin"] = "user_confirmed_rule"
            task["basis"] = "explicit"
            task["period_kind"] = task.get("period_kind") or revised["period_kind"]
            if revised["target_periods"]:
                task.update(
                    period_kind=revised["period_kind"],
                    target_periods=revised["target_periods"],
                    all_quarters=revised["all_quarters"],
                )
            task.pop("semantic_review", None)
            task.pop("pending_periods", None)
            if task.get("target_periods"):
                task["target_periods"] = revised["target_periods"] or [
                    f"{year}/Q{quarter}" for year in task["target_years"] for quarter in range(1, 5)
                ]
        elif _RELATIVE.search(response):
            revised = resolve(
                response, clock, confirmed=task if task.get("basis") == "calendar_year" else None
            )
            task = {
                **task,
                **{
                    k: revised[k]
                    for k in ("target_years", "relative_count", "basis", "unresolved", "origin")
                },
            }
            task.pop("semantic_review", None)
            task.pop("pending_periods", None)
        elif task.get("pending_periods") and re.fullmatch(
            r"(?:是|是的|对|确认|好的|好|可以|按这个|yes|y)[。！!\s]*", response, re.IGNORECASE
        ):
            task = {
                **task,
                "target_years": explicit_years(" ".join(task["pending_periods"])),
                "unresolved": [],
                "origin": "user_confirmed_rule",
                "basis": "explicit",
            }
            task.pop("semantic_review", None)
            task.pop("pending_periods", None)
        elif task.get("unresolved") == ["relative_period_basis"] and re.fullmatch(
            r"(?:是|是的|对|确认|好的|好|可以|按这个|yes|y)[。！!\s]*", response, re.IGNORECASE
        ):
            task = {
                **task,
                "basis": "calendar_year",
                "unresolved": [],
                "origin": "user_confirmed_rule",
            }
        elif re.search(r"保留.*?缺口|允许缺失|缺.*?(?:标|说明)", response):
            task = {**task, "accept_partial": True}
        elif task.get("unresolved"):
            task["semantic_review"] = True  # 交回同一 Agent 解释补充，不能把任意回复当作同意。
        # “否/滚动”等不能误当确认；由 Agent 继续提出明确候选。
    task["processed_responses"] = sorted(processed)
    if task.get("all_quarters"):
        task["target_periods"] = [
            f"{year}/Q{quarter}" for year in task["target_years"] for quarter in range(1, 5)
        ]
    return task


def save(root: Path, row: dict[str, Any], turn_id: str, task: dict[str, Any]) -> None:
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.append_agent_event(
            connection,
            str(row["id"]),
            "question_requirements",
            json.dumps(task, ensure_ascii=False),
            storage.now_utc(),
            turn_id=turn_id,
        )


def question(task: dict[str, Any], *, final_check: bool = False) -> str | None:
    if task.get("semantic_review") and not final_check:
        return None
    if task.get("unresolved"):
        if (
            "period_range" in task["unresolved"]
            and "relative_period_basis" not in task["unresolved"]
        ):
            return "这项计算需要确定期间，请给出具体年份/季度范围；如果按最近完整年度计算，也请说明年度数量。"
        years = "、".join(map(str, task.get("target_years", [])))
        return f"你说的近{task.get('relative_count')}年，是指{years}这些完整自然年度，还是按公司财年、滚动期间或其他年份？请确认具体范围。"
    return None
