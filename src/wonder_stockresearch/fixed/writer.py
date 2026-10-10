"""阶段 7 的小型 DeepSeek 写作入口。

只从指定 run 的已归档来源构造受限上下文，调用 DeepSeek 生成既有 ResearchDraft，
再复用 research.save_draft/validate 保存与校验草稿；不自动 finish。
凭据只从环境变量 DEEPSEEK_API_KEY 读取，不写入项目、日志、数据库或对话。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from decimal import Decimal
from functools import partial
from itertools import pairwise
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from wonder_stockresearch import (
    envfile,
    model_request,
    observability,
    progress,
    skill_registry,
    storage,
)
from wonder_stockresearch.financial_calculations import calculate_trusted_metrics, numeric_inputs
from wonder_stockresearch.fixed import research
from wonder_stockresearch.fixed.fixed_quality import QUESTION_GROUPS, draft_hash
from wonder_stockresearch.fixed.questions import MOAT_DEFINITIONS, MOAT_SCORE_ANCHORS, QUESTION_TEXT
from wonder_stockresearch.models import (
    GATE_IDS,
    MOAT_IDS,
    QUESTION_IDS,
    Answer,
    DraftReview,
    ResearchDraft,
    ResearchPacket,
    ReviewIssue,
    Source,
    source_id_text,
)

DEFAULT_BASE_URL = model_request.DEFAULT_BASE_URL
DEFAULT_MAX_TOKENS = model_request.DEFAULT_MAX_TOKENS
DEFAULT_MODEL = model_request.DEFAULT_MODEL
DEFAULT_TEMPERATURE = model_request.DEFAULT_TEMPERATURE
DEFAULT_THINKING = model_request.DEFAULT_THINKING
REQUEST_CONNECT_TIMEOUT_SECONDS = model_request.REQUEST_CONNECT_TIMEOUT_SECONDS
REQUEST_READ_TIMEOUT_SECONDS = model_request.REQUEST_READ_TIMEOUT_SECONDS
THINKING_MODES = model_request.THINKING_MODES
requests = model_request.requests

PROMPT_TEMPLATE_VERSION = "deepseek-draft-v3"
STAGED_TEMPLATE_VERSION = "deepseek-staged-v9"
# 首轮写作是 6 个问题分组、1 个综合结论和 1 次审查。审查若分别指出
# 6 个分组都有实质错误，必须仍能各修订一次并完成复核，不能因预算少 1
# 个单元而直接留下 failed draft。
MAX_MODEL_CALLS = 15
MAX_REVISION_UNITS = 6
MAX_SOURCES = 20
MAX_EXCERPT_CHARS = 4000
MAX_PROVIDER_EXCERPT_CHARS = 120000
MAX_TOTAL_EXCERPT_CHARS = 300000
ALLOWED_PROVIDERS = ("deepseek",)


class AnswerSection(BaseModel):
    """分组请求和响应共用同一个对象契约。"""

    model_config = ConfigDict(extra="forbid")
    answers: list[Answer]


_EXAMPLE = (
    '{"schema_version":1,"ticker":"HK.00728","as_of":"2026-09-20T00:00:00+00:00",'
    '"answers":[{"question_id":"Q01","status":"answered","statements":[{"id":"q01-1",'
    '"kind":"fact","text":"商业模式概述","evidence":[{"source_id":"<source_id>",'
    '"locator":"lines 1-20","relation":"supports"}],"numbers":[]}],"gap":null}],'
    '"gates":[{"gate_id":"G1","result":"pass","rationale":{"id":"g1-1","kind":"fact",'
    '"text":"理由","evidence":[{"source_id":"<source_id>","locator":"lines 1-20",'
    '"relation":"supports"}],"numbers":[]},"gap":null}],'
    '"moats":[{"dimension_id":"M1","score":null,"confidence":"low","rationale":'
    '{"id":"m1-1","kind":"inference","text":"依据不足","evidence":[],"numbers":[]},'
    '"gap":{"missing":"单位成本对比","where_to_find":"年报成本明细","next_check":"下次年报",'
    '"impact":"无法评分"}}],"challenge":{"counterargument":{"id":"c1","kind":"inference",'
    '"text":"最强反方","evidence":[],"numbers":[]},"channels_checked":["已归档来源"],'
    '"search_scope":"仅本次归档材料","refutation_found":false,"overturning_evidence":"描述",'
    '"next_check":"下次定期报告"},"conflicts":[],"conclusion":{"id":"concl","kind":"inference",'
    '"text":"结论","evidence":[],"numbers":[]},"review_explanations":[]}'
)


class WriterError(model_request.ModelRequestError):
    """写作入口错误；exit_code 2 表示输入/凭据问题，1 表示模型或网络失败。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = 1,
        validation_error_count: int | None = None,
        validation_details: tuple[str, ...] | None = None,
    ) -> None:
        self.validation_error_count = validation_error_count
        self.validation_details = validation_details or ()
        super().__init__(code, message, exit_code=exit_code)


def _system_rules() -> str:
    questions = "、".join(QUESTION_IDS)
    question_definitions = "\n".join(
        f"{question_id}: {QUESTION_TEXT[question_id]}" for question_id in QUESTION_IDS
    )
    gates = "、".join(GATE_IDS)
    moats = "、".join(MOAT_IDS)
    return (
        "你是公司研究写作器，只能依据用户提供的已归档来源片段生成 JSON。\n"
        f"必须覆盖问题 {questions}；公司门闸 {gates}；护城河维度 {moats}；缺一不可。\n"
        "以下是各问题的完整定义；每个答案必须直接回答对应问题，不得把相邻问题或常见财务指标错放到该问题：\n"
        f"{question_definitions}\n"
        "Q03 专门分析资本投入：优先给出资本开支/资本支出金额、占收入比例、对应期间和计算口径；"
        "若缺少分子、分母或可比期间，明确标出缺口。利润率、ROE、净利润或 EPS 不能替代资本开支分析。\n"
        "Q04 专门分析技术替代和产品变化：说明变化/替代发生的速度、受影响的业务或产品、"
        "以及对竞争地位的具体影响；EPS、利润率或估值指标本身不能回答技术替代问题。"
        "找不到直接证据时将答案降为 partial/missing，不用其他指标填满。\n"
        "Q01 商业模式应优先依据研究截止时点前已发布的年报/中报等一手披露；"
        "as_of 之后取得的 Futu profile 快照只能作背景，不能支撑 answered。\n"
        "M1 只在存在可核验的单位成本或成本结构优势证据时才给非 null 分数；"
        "网络覆盖广、资产规模大或持有牌照本身不等于成本优势。\n"
        "每个 answered 问题和每个非 null 护城河评分，至少要有一条 relation=supports 的合格来源引用；"
        "被标为 context 的来源不能支撑这些结果。缺少合格引用时降级，不得为了覆盖率保留强结论。\n"
        "输出前逐项自查：编号、问题含义、陈述、数字与引用是否属于同一个问题；"
        "发现错位时重写对应答案或明确记录证据缺口。\n"
        "证据引用只能使用给出的 source_id 与 locator，relation 取 supports/refutes/context；"
        "不得发明来源、页码/行号、发布日期或数字。\n"
        "标记 kind=provider 的 JSON 是结构化事实包（财报、主营构成、分红、股东、估值等），"
        "量化问题优先采用其中的数值，locator 用该数据集的 period/field；"
        "PDF 片段用于商业模式、风险、治理等定性引用。\n"
        "provider 里 evidence_class=reported（带 available_from）的可作 supports；"
        "evidence_class=snapshot（估值、分析师评级、行情快照、板块）只能作 context，"
        "不能作为 answered 问题、pass/fail 门闸或非 null 评分的 supports。\n"
        "资料不足时：问题用 partial/missing 并给 gap；门闸用 insufficient；护城河评分用 null。\n"
        "Answer 的 gap 只能是 null 或完整的 Gap 对象，不能是字符串、数组或空字符串。\n"
        "status=answered 时必须使用 gap=null；status=partial 或 missing 时，gap 必须同时包含\n"
        "missing、where_to_find、next_check、impact 四个非空字符串字段。示例：\n"
        '{"question_id":"Q01","status":"answered","statements":[],"gap":null}\n'
        '{"question_id":"Q02","status":"partial","statements":[],"gap":{'
        '"missing":"缺少分业务收入拆分","where_to_find":"下一份年报或业绩公告",'
        '"next_check":"下一次定期报告","impact":"暂时无法判断增长质量"}}。\n'
        '{"question_id":"Q03","status":"missing","statements":[],"gap":{'
        '"missing":"没有可用的客户集中度数据","where_to_find":"年报风险因素或主营构成",'
        '"next_check":"下一次年报披露时检查","impact":"无法判断客户依赖风险"}}。\n'
        "若给出数字，numbers 的每一项必须完整包含 value（字符串）、unit、period、basis、evidence；"
        "currency 可为 null；缺任何必填字段都会被程序拒绝。\n"
        "所有 evidence 字段都必须是数组（即使只有一个引用），元素形如 "
        '{"source_id":"...","locator":"...","relation":"supports"}；'
        "numbers 的每一项也必须带 evidence 数组，不能写成对象或字符串。\n"
        "不得输出 valuation/target_price/action，也不要计算总分（由程序派生）。\n"
        f"输出必须是单个 JSON 对象。结构示例：{_EXAMPLE}"
    )


def _is_structured_provider(source: Source) -> bool:
    return source.kind == "provider" or bool(source.metadata.get("dataset"))


def _source_excerpt(root: Any, source: Source) -> tuple[str, str] | None:
    if not source.text_path:
        return None
    path = root / source.text_path
    if not path.is_file():
        return None
    cap = MAX_PROVIDER_EXCERPT_CHARS if _is_structured_provider(source) else MAX_EXCERPT_CHARS
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None
    if len(text) > cap:
        text = text[:cap] + "\n…（已截断）"
    line_count = text.count("\n") + 1
    return text, f"lines 1-{line_count}"


def _build_messages(root: Any, run: dict[str, Any], sources: list[Source]) -> tuple[str, str]:
    briefs: list[dict[str, Any]] = []
    blocks: list[str] = []
    total_chars = 0
    for source in sources[:MAX_SOURCES]:
        source_id = source_id_text(source.id)
        evidence_class = source.metadata.get("evidence_class") or (
            "reported" if source.metadata.get("available_from") else "snapshot"
        )
        brief: dict[str, Any] = {
            "source_id": source_id,
            "kind": source.kind,
            "title": source.title,
            "published_at": source.published_at.isoformat() if source.published_at else None,
            "evidence_class": evidence_class,
            "available_from": source.metadata.get("available_from"),
            "observed_after_as_of": source.metadata.get("observed_after_as_of"),
        }
        excerpt = _source_excerpt(root, source)
        if excerpt is not None and total_chars < MAX_TOTAL_EXCERPT_CHARS:
            text, locator = excerpt
            remaining = MAX_TOTAL_EXCERPT_CHARS - total_chars
            text = text[:remaining]
            total_chars += len(text)
            brief["locator_hint"] = locator
            blocks.append(
                f"[{source_id}] {source.title}（{source.kind}；发布 {brief['published_at']}）"
                f"\nlocator: {locator}\n{text}"
            )
        briefs.append(brief)
    as_of = storage.parse_iso(run["as_of"])
    support_ids = [
        source_id_text(s.id) for s in sources[:MAX_SOURCES] if research._is_core_eligible(s, as_of)
    ]
    context_ids = [
        source_id_text(s.id)
        for s in sources[:MAX_SOURCES]
        if not research._is_core_eligible(s, as_of)
    ]
    user = (
        f"ticker={run['ticker']}\nas_of={run['as_of']}\n\n"
        "来源清单（只能用这些 source_id；locator 用 locator_hint 或片段内的行号）：\n"
        f"{json.dumps(briefs, ensure_ascii=False)}\n\n"
        f"可作 supports 的 source_id（answered/pass-fail/非 null 评分只能用这些）：\n{support_ids}\n"
        f"仅可作 context 的 source_id：\n{context_ids}\n\n"
        "已归档来源内容（PDF 为分段截取；kind=provider 为结构化 JSON）：\n"
        f"{chr(10).join(blocks) if blocks else '（本次没有可用内容，请把相关结论标为 partial/missing）'}\n\n"
        "请输出完整的 ResearchDraft JSON。"
    )
    return _system_rules(), user


def _input_hash(system: str, user: str) -> str:
    canonical = f"{PROMPT_TEMPLATE_VERSION}\n{system}\n{user}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _selected_skill_context(root: Any, run_id: str) -> tuple[str, list[dict[str, object]]]:
    """从 run 的审计元数据恢复选中方法，正文始终重新按需读取，不写入数据库。"""
    with storage.open_db(root) as connection:
        steps = storage.list_steps(connection, run_id)
    selection: list[dict[str, object]] | None = None
    for step in reversed(steps):
        if (
            step["step_key"] != "skill_selection"
            or step["status"] != "succeeded"
            or not step["metadata_json"]
        ):
            continue
        try:
            metadata = json.loads(str(step["metadata_json"]))
        except json.JSONDecodeError:
            continue
        raw_skills = metadata.get("skills") if isinstance(metadata, dict) else None
        if isinstance(raw_skills, list) and all(isinstance(item, dict) for item in raw_skills):
            selection = [dict(item) for item in raw_skills]
            break
    if not selection:
        return "", []
    names = [str(item.get("name") or "") for item in selection]
    if any(not name for name in names):
        raise WriterError("invalid_skill_selection", "run 的 Skill 审计记录损坏", exit_code=2)
    try:
        loaded = skill_registry.load_selected(root, names)
    except skill_registry.SkillRegistryError as error:
        raise WriterError(error.code, error.message, exit_code=2) from error
    current_audit = skill_registry.selection_audit(loaded)
    normalized_selection = []
    for index, item in enumerate(selection):
        previous = dict(item)
        # schema v5–v9 不记录 manifest 扩展字段；兼容既有固定研究 Skill 元数据。
        previous.setdefault("tools", [])
        if index < len(current_audit):
            for field in ("applies_to", "dependencies", "prompt_resources"):
                previous.setdefault(field, current_audit[index][field])
        normalized_selection.append(previous)
    if current_audit != normalized_selection:
        raise WriterError(
            "skill_changed_since_selection",
            "Skill 内容已在本次 run 创建后改变；请创建新 run",
            exit_code=2,
        )
    context = skill_registry.execution_context(loaded)
    if len(context) > 30000:
        raise WriterError(
            "skill_context_too_large",
            "选择的 Skill 正文和参考资料超过 Writer 上下文上限",
            exit_code=2,
        )
    return context, current_audit


def _with_skill_context(messages: list[dict[str, str]], context: str) -> list[dict[str, str]]:
    if not context:
        return messages
    augmented = [dict(message) for message in messages]
    augmented[0]["content"] += (
        "\n\n以下是当前选择的项目方法和参考资料。它只能补充分析方法，"
        "不能改变已有证据边界、输出 schema、数值来源或定稿权限：\n" + context
    )
    return augmented


def _writer_request(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """保留 Writer 的错误契约，传输实现由公共模块负责。"""
    try:
        return call(*args, **kwargs)
    except model_request.ModelRequestError as error:
        if isinstance(error, WriterError):
            raise
        raise WriterError(error.code, error.message, exit_code=error.exit_code) from error


def _api_key() -> str:
    return _writer_request(model_request.api_key)


def _base_url() -> str:
    return model_request.base_url()


def _resolved_model(model: str | None) -> str:
    return model_request.resolved_model(model)


def _max_tokens() -> int:
    return model_request.max_tokens()


def _resolve_thinking(value: str | None) -> str:
    return _writer_request(model_request.resolve_thinking, value)


def _resolve_temperature(value: float | None) -> float:
    return _writer_request(model_request.resolve_temperature, value)


def _request_deepseek(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    thinking: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """兼容既有 Writer 离线注入点；实际 HTTP 与配置在公共请求模块。"""
    return _writer_request(
        model_request.request_deepseek,
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        thinking=thinking,
        max_tokens=max_tokens,
    )


def _extract_content(response: dict[str, Any]) -> str:
    return _writer_request(model_request.extract_content, response)


def _escape_unquoted_inner_quotes(content: str) -> str:
    """只转义字符串内部显然不是 JSON 结束符的双引号，不改字段或事实内容。"""
    result: list[str] = []
    in_string = False
    escaped = False
    for index, character in enumerate(content):
        if escaped:
            result.append(character)
            escaped = False
            continue
        if character == "\\" and in_string:
            result.append(character)
            escaped = True
            continue
        if character != '"':
            result.append(character)
            continue
        if not in_string:
            result.append(character)
            in_string = True
            continue
        following = content[index + 1 :].lstrip()
        if not following or following[0] in ":,}]":
            result.append(character)
            in_string = False
        else:
            result.append('\\"')
    return "".join(result)


def _repair_mismatched_json_closers(content: str) -> str:
    """把字符串外与当前容器类型不匹配的 ]/} 改为正确结束符。"""
    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escaped = False
    for index, character in enumerate(content):
        if escaped:
            result.append(character)
            escaped = False
            continue
        if character == "\\" and in_string:
            result.append(character)
            escaped = True
            continue
        if character == '"':
            result.append(character)
            in_string = not in_string
            continue
        if in_string:
            result.append(character)
            continue
        if character in "[{":
            stack.append(character)
            result.append(character)
            continue
        if character in "]}" and stack:
            expected = "]" if stack[-1] == "[" else "}"
            following = content[index + 1 :].lstrip()
            # 模型常在正确关闭 gap 对象后多写一个 ]，随后仍会给出该 Answer 的 }。
            # 此时跳过冗余符号，不能把它替换为 }，否则会提前关闭 answers 数组。
            previous = "".join(result).rstrip()
            if (
                character != expected
                and following.startswith(expected)
                and previous.endswith(expected)
            ):
                continue
            stack.pop()
            result.append(expected)
            continue
        result.append(character)
    return "".join(result)


def _json_object(content: str) -> dict[str, Any]:
    """兼容模型偶尔包裹的 Markdown fence/简短前言，仍只接受一个 JSON 对象。"""
    stripped = content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline >= 0 else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        repaired = _repair_mismatched_json_closers(_escape_unquoted_inner_quotes(stripped))
        if repaired != stripped:
            try:
                payload = json.loads(repaired)
            except json.JSONDecodeError:
                pass
            else:
                if not isinstance(payload, dict):
                    raise TypeError("模型输出必须是 JSON 对象")
                return payload
        start = stripped.find("{")
        if start < 0:
            raise
        payload, end = json.JSONDecoder().raw_decode(stripped[start:])
        if stripped[start + end :].strip().strip("`").strip():
            raise json.JSONDecodeError("JSON 对象后存在额外内容", stripped, start + end)
    if not isinstance(payload, dict):
        raise TypeError("模型输出必须是 JSON 对象")
    return payload


def _json_objects(content: str) -> list[dict[str, Any]]:
    """从模型连续输出的多个 JSON 对象中恢复对象序列。"""
    decoder = json.JSONDecoder()
    values: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(content):
        start = content.find("{", cursor)
        if start < 0:
            break
        try:
            value, size = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(value, dict):
            values.append(value)
        cursor = start + size
    return values


def _parse_draft(content: str, run: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = _json_object(content)
    except json.JSONDecodeError as error:
        raise WriterError("invalid_json", "模型输出不是合法 JSON") from error
    if isinstance(payload, dict) and isinstance(payload.get("draft"), dict):
        payload = payload["draft"]
    if not isinstance(payload, dict):
        raise WriterError("invalid_json", "模型输出不是 JSON 对象")
    # Python 控制身份与时间边界，模型不能改写 ticker/as_of/schema_version
    payload["schema_version"] = 1
    payload["ticker"] = run["ticker"]
    payload["as_of"] = run["as_of"]
    return payload


def _validate_draft(payload: dict[str, Any]) -> ResearchDraft:
    try:
        return ResearchDraft.model_validate(payload)
    except ValidationError as error:
        errors = error.errors()
        detail_items = tuple(
            f"{'.'.join(str(part) for part in item.get('loc', ()))}：{item.get('msg', '')}"
            for item in errors[:3]
        )
        details = "；".join(detail_items)
        raise WriterError(
            "invalid_schema",
            f"模型输出不符合 ResearchDraft（共 {len(errors)} 处）：{details}",
            validation_error_count=len(errors),
            validation_details=detail_items,
        ) from error


def _validation_summary(error: WriterError, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "error_count": error.validation_error_count,
        "details": list(error.validation_details),
    }


def _model_attempt_summary(kind: str, response: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": kind,
        "usage": _usage_summary(response),
        "finish_reason": _finish_reason(response),
    }


def _schema_repair_messages(payload: dict[str, Any], error: WriterError) -> list[dict[str, str]]:
    system = (
        "你是 ResearchDraft JSON schema 修复器。只修复用户指出的结构问题，返回完整的 ResearchDraft JSON。\n"
        "不得改变任何事实、数字、结论、source_id、locator、ticker 或 as_of；不得新增来源或证据。\n"
        "Answer.gap 只能是 null 或对象；对象必须完整包含四个非空字符串字段："
        "missing、where_to_find、next_check、impact。status=answered 时 gap 必须为 null；"
        "status=partial 或 missing 时 gap 必须是上述完整对象。不得把 gap 写成字符串或数组。\n"
        "保留原 JSON 中所有未涉及字段，不要省略 answers、gates、moats、challenge、conflicts、"
        "conclusion 和 review_explanations。只输出 JSON 对象，不要输出解释或 Markdown。"
    )
    user = json.dumps(
        {
            "validation_errors": list(error.validation_details),
            "draft": payload,
        },
        ensure_ascii=False,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _gap_repair_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy whose explicitly repairable gap values have the same marker."""
    projection = deepcopy(payload)
    for collection_name in ("answers", "gates", "moats"):
        collection = projection.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if isinstance(item, dict) and "gap" in item:
                item["gap"] = "<repairable-gap>"
    return projection


def _ensure_repair_preserved_content(original: dict[str, Any], repaired: dict[str, Any]) -> None:
    if _gap_repair_projection(original) == _gap_repair_projection(repaired):
        return
    raise WriterError(
        "invalid_schema",
        "自动修复改变了 gap 以外的研究内容，拒绝保存",
        validation_error_count=1,
        validation_details=("schema_repair：除 gap 外的字段发生变化",),
    )


def _usage_summary(response: dict[str, Any]) -> dict[str, Any] | None:
    return model_request.usage_summary(response)


def _finish_reason(response: dict[str, Any]) -> str | None:
    return model_request.finish_reason(response)


def _visible_response_content(response: dict[str, Any]) -> str | None:
    return model_request.visible_response_content(response)


def _traced_request(
    root: Any,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    thinking: str | None,
    kind: str,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """保留 Writer 注入点与错误类型，共用一次请求的 trace 实现。"""
    return _writer_request(
        model_request.traced_request,
        Path(root),
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        thinking=thinking,
        kind=kind,
        max_tokens=max_tokens,
        request=_request_deepseek,
    )


def _write_one_shot(
    root: Any,
    run_id: str,
    *,
    provider: str = "deepseek",
    model: str | None = None,
    temperature: float | None = None,
    thinking: str | None = None,
    on_progress: progress.ProgressCallback | None = None,
) -> tuple[dict[str, Any], list[research.ValidationIssue]]:
    """生成并保存草稿；返回 (data, issues)。只 save-draft/validate，不 finish。"""
    if provider not in ALLOWED_PROVIDERS:
        raise WriterError("unsupported_provider", f"不支持的 provider：{provider}", exit_code=2)
    envfile.load_env_file(root / ".env")
    api_key = _api_key()
    resolved_model = _resolved_model(model)
    resolved_thinking = _resolve_thinking(thinking)
    resolved_temperature = _resolve_temperature(temperature)
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = research._require_run(connection, run_id)
        if run["status"] == "completed":
            raise research.ResearchError("run_completed", "已完成的研究不能生成草稿", exit_code=2)
        if run["status"] != "draft":
            raise research.ResearchError("invalid_status", "只有 draft 可以生成草稿", exit_code=2)
        rows = storage.get_run_sources(connection, run_id)
        sources = [Source.model_validate_json(row["source_json"]) for row in rows]
    with observability.span(
        Path(root),
        "skills.load",
        span_kind="chain",
        attributes={"execution": "writer"},
    ) as skill_span:
        skill_context, skill_audit = _selected_skill_context(root, run_id)
        skill_span.set_attributes(observability.skill_audit_summary(skill_audit))
    progress.emit(
        on_progress,
        "write-draft",
        "started",
        f"已读取 {len(sources)} 个归档来源，准备调用 DeepSeek",
        run_id=run_id,
    )
    system, user = _build_messages(root, run, sources)
    system = _with_skill_context([{"role": "system", "content": system}], skill_context)[0][
        "content"
    ]
    input_hash = _input_hash(system, user)
    attempt = research._begin_step(
        root, run_id, "write_draft", provider, input_hash, model=resolved_model
    )
    model_attempts: list[dict[str, Any]] = []
    repair_attempted = False
    initial_validation: dict[str, Any] | None = None
    final_validation: dict[str, Any] | None = None
    try:
        progress.emit(
            on_progress, "write-draft", "running", "DeepSeek 正在生成结构化草稿", run_id=run_id
        )
        response = _traced_request(
            root,
            api_key=api_key,
            model=resolved_model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=resolved_temperature,
            thinking=resolved_thinking,
            kind="initial",
        )
        model_attempts.append(_model_attempt_summary("initial", response))
        content = _extract_content(response)
        payload = _parse_draft(content, run)
        try:
            draft = _validate_draft(payload)
            final_validation = {"status": "passed", "error_count": 0, "details": []}
        except WriterError as error:
            if error.code != "invalid_schema":
                raise
            repair_attempted = True
            initial_validation = _validation_summary(error, "failed")
            progress.emit(
                on_progress,
                "write-draft",
                "running",
                "结构校验未通过，DeepSeek 正在尝试一次受控修复",
                run_id=run_id,
            )
            repair_response = _traced_request(
                root,
                api_key=api_key,
                model=resolved_model,
                messages=_schema_repair_messages(payload, error),
                temperature=0.0,
                thinking="disabled",
                kind="schema_repair",
            )
            model_attempts.append(_model_attempt_summary("schema_repair", repair_response))
            try:
                repaired_payload = _parse_draft(_extract_content(repair_response), run)
                _ensure_repair_preserved_content(payload, repaired_payload)
                draft = _validate_draft(repaired_payload)
            except WriterError as repair_error:
                if repair_error.code in {"invalid_json", "invalid_schema", "empty_content"}:
                    final_validation = _validation_summary(repair_error, "failed")
                    raise WriterError(
                        "invalid_schema",
                        f"模型输出不符合 ResearchDraft，且一次自动修复后仍失败：{repair_error}",
                        validation_error_count=repair_error.validation_error_count,
                        validation_details=repair_error.validation_details,
                    ) from repair_error
                raise
            response = repair_response
            final_validation = {"status": "passed", "error_count": 0, "details": []}
        progress.emit(
            on_progress,
            "write-draft",
            "running",
            "模型输出已通过 ResearchDraft 结构校验",
            run_id=run_id,
        )
        data, issues = research.save_draft(root, run_id, ResearchPacket(draft=draft, sources=[]))
        metadata = {
            "template_version": PROMPT_TEMPLATE_VERSION,
            "thinking": resolved_thinking,
            "temperature": resolved_temperature,
            "usage": _usage_summary(response),
            "finish_reason": _finish_reason(response),
            "validation_issue_count": len(issues),
            "model_attempts": model_attempts,
            "repair_attempted": repair_attempted,
            "initial_validation": initial_validation,
            "final_validation": final_validation,
            "skills": skill_audit,
        }
        research._end_step(
            root,
            run_id,
            "write_draft",
            attempt,
            "succeeded",
            output_ref=f"draft:{run_id}",
            metadata=metadata,
        )
        progress.emit(
            on_progress,
            "write-draft",
            "succeeded" if not issues else "warning",
            "草稿已保存，正在等待 validate 结果"
            if not issues
            else f"草稿已保存，但有 {len(issues)} 个问题",
            run_id=run_id,
        )
        return {
            **data,
            "provider": provider,
            "model": resolved_model,
            "thinking": resolved_thinking,
            "temperature": resolved_temperature,
            "input_hash": input_hash,
            "template_version": PROMPT_TEMPLATE_VERSION,
            "usage": metadata["usage"],
            "issue_count": len(issues),
        }, issues
    except WriterError as error:
        failure_metadata = {
            "template_version": PROMPT_TEMPLATE_VERSION,
            "model_attempts": model_attempts,
            "repair_attempted": repair_attempted,
            "initial_validation": initial_validation,
            "final_validation": final_validation,
            "skills": skill_audit,
        }
        research._end_step(
            root,
            run_id,
            "write_draft",
            attempt,
            "failed",
            error=_error_payload(error),
            metadata=failure_metadata,
        )
        raise
    except research.ResearchError as error:
        research._end_step(
            root, run_id, "write_draft", attempt, "failed", error=_error_payload(error)
        )
        raise


def _load_run_sources(root: Any, run_id: str) -> tuple[dict[str, Any], list[Source]]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        run = research._require_run(connection, run_id)
        if run["status"] != "draft":
            code = "run_completed" if run["status"] == "completed" else "invalid_status"
            raise research.ResearchError(code, "只有 draft 可以生成或审查草稿", exit_code=2)
        rows = storage.get_run_sources(connection, run_id)
    return run, [Source.model_validate_json(row["source_json"]) for row in rows]


def _tracked_request(
    root_or_tracker: Any,
    tracker: dict[str, Any] | None = None,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    thinking: str | None,
    kind: str,
) -> dict[str, Any]:
    # 保留旧的私有测试/调用形状 ``_tracked_request(tracker, ...)``；正常写作路径
    # 传入 ``root, tracker`` 以便将 LLM span 归入当前 research trace。
    root: Any
    if tracker is None:
        tracker = root_or_tracker
        root = Path(".")
    else:
        root = root_or_tracker
    if tracker["calls"] >= MAX_MODEL_CALLS:
        raise WriterError("call_budget_exhausted", f"模型调用已达到硬上限 {MAX_MODEL_CALLS}")
    response = _traced_request(
        root,
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        thinking=thinking,
        kind=kind,
    )
    tracker["calls"] += 1
    tracker["attempts"].append(_model_attempt_summary(kind, response))
    usage = _usage_summary(response) or {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        tracker["usage"][key] = tracker["usage"].get(key, 0) + int(usage.get(key, 0))
    return response


def _section_get(root: Any, run_id: str, section_key: str, input_hash: str) -> Any | None:
    with storage.open_db(root) as connection:
        row = storage.get_draft_section(connection, run_id, section_key, input_hash)
    return json.loads(row["output_json"]) if row else None


def _section_save(
    root: Any,
    run_id: str,
    section_key: str,
    input_hash: str,
    output: Any,
    provider: str,
    model: str,
) -> None:
    rendered = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.save_draft_section(
            connection,
            run_id=run_id,
            section_key=section_key,
            input_hash=input_hash,
            output_json=rendered,
            output_hash=hashlib.sha256(rendered.encode()).hexdigest(),
            provider=provider,
            model=model,
            template_version=STAGED_TEMPLATE_VERSION,
            created_at=storage.now_utc(),
        )


def _calculation_reference_keys(calculations: list[Any]) -> set[tuple[str, str]]:
    """可信计算随 Prompt 输入时，其可追溯原始输入也属于允许引用的证据。"""
    return {
        (source_id_text(reference.source_id), reference.locator)
        for calculation in calculations
        for reference in calculation.source_refs
    }


def _validate_answer_evidence(
    answers: list[Answer], pack: Any, calculations: list[Any] | None = None
) -> None:
    """分组答案只能引用本组 EvidencePack 或随之输入的可信计算原始来源。"""
    if not pack.chunks:
        # 离线 schema fixture 没有归档文本；保留其对生命周期的回归覆盖。
        return
    chunks = {(chunk.source_id, chunk.locator): chunk for chunk in pack.chunks}
    calculation_refs = _calculation_reference_keys(calculations or [])
    invalid: list[str] = []
    for answer in answers:
        for statement in answer.statements:
            references = [
                *statement.evidence,
                *(ref for number in statement.numbers for ref in number.evidence),
            ]
            for reference in references:
                key = (source_id_text(reference.source_id), reference.locator)
                chunk = chunks.get(key)
                if chunk is None:
                    if key not in calculation_refs:
                        invalid.append(
                            f"{answer.question_id}/{statement.id}: 不在 EvidencePack 或可信计算输入的 {key[0]} {key[1]}"
                        )
                elif reference.relation == "supports" and not chunk.support_eligible:
                    invalid.append(
                        f"{answer.question_id}/{statement.id}: 不可作 supports 的 {key[0]} {key[1]}"
                    )
    if invalid:
        raise WriterError(
            "evidence_outside_pack",
            "分组答案引用越出证据包或错误使用 context：" + "；".join(invalid[:4]),
        )


def _validate_answer_semantics(answers: list[Answer]) -> None:
    """`answered` 表示该题没有未解决的题意缺口，不能同时携带 gap。"""
    invalid = [
        answer.question_id
        for answer in answers
        if answer.status == "answered" and answer.gap is not None
    ]
    if invalid:
        raise WriterError(
            "answered_with_gap",
            f"{', '.join(invalid)} 标为 answered 时 gap 必须为 null；存在缺口应标为 partial 或 missing",
        )


def _validate_synthesis_evidence(
    draft: ResearchDraft,
    packs: dict[str, Any],
    calculations: list[Any] | None = None,
) -> None:
    """综合判断不得把未输入的来源或 context 伪装为支持性证据。"""
    chunks = {
        (chunk.source_id, chunk.locator): chunk for pack in packs.values() for chunk in pack.chunks
    }
    if not chunks:
        # 离线 schema fixture 没有归档文本；保留其对生命周期的回归覆盖。
        return
    calculation_refs = _calculation_reference_keys(calculations or [])
    invalid: list[str] = []
    statements = [gate.rationale for gate in draft.gates]
    statements.extend(moat.rationale for moat in draft.moats)
    statements.extend((draft.challenge.counterargument, draft.conclusion))
    statements.extend(item.statement for item in draft.review_explanations)
    for statement in statements:
        references = [
            *statement.evidence,
            *(ref for number in statement.numbers for ref in number.evidence),
        ]
        for reference in references:
            key = (source_id_text(reference.source_id), reference.locator)
            chunk = chunks.get(key)
            if chunk is None:
                if key not in calculation_refs:
                    invalid.append(
                        f"{statement.id}: 不在 EvidencePack 或可信计算输入的 {key[0]} {key[1]}"
                    )
            elif reference.relation == "supports" and not chunk.support_eligible:
                invalid.append(f"{statement.id}: 不可作 supports 的 {key[0]} {key[1]}")
    if invalid:
        raise WriterError(
            "evidence_outside_pack",
            "综合判断引用越出证据包或错误使用 context：" + "；".join(invalid[:4]),
        )


def _parse_answers(content: str, expected: tuple[str, ...]) -> list[Answer]:
    try:
        try:
            payloads = [_json_object(content)]
        except json.JSONDecodeError as original_error:
            # 只兼容完整的连续 Answer 对象；损坏的外层 JSON 不能被扫描为内部 Statement。
            decoder = json.JSONDecoder()
            remaining = content.strip()
            payloads = []
            try:
                while remaining:
                    value, end = decoder.raw_decode(remaining)
                    if not isinstance(value, dict):
                        raise TypeError("分组必须为 JSON 对象")
                    payloads.append(value)
                    remaining = remaining[end:].strip()
            except json.JSONDecodeError:
                raise original_error from None
        raw: list[Answer] = []
        for payload in payloads:
            # 兼容历史完整草稿包装；只解开明确字段，不扫描任意嵌套对象。
            for wrapper in ("draft", "result"):
                if isinstance(payload.get(wrapper), dict) and "answers" in payload[wrapper]:
                    payload = payload[wrapper]
                    break
            if "question_id" in payload:
                payload = {"answers": [payload]}
            if "answers" not in payload:
                raise WriterError(
                    "invalid_section_shape",
                    "分组响应缺少 answers 字段；需要 {answers: [...]} 对象，不能返回 schema 或其他包装",
                )
            raw.extend(AnswerSection.model_validate(payload).answers)
        answers = _normalize_answer_numbers(raw)
    except json.JSONDecodeError as error:
        stripped = content.strip()
        context = stripped[max(0, error.pos - 60) : error.pos + 60].replace("\n", " ")
        diagnostic = (
            f"{error.msg}（位置 {error.pos}/{len(stripped)}；"
            f"首字符={stripped[:1]!r}，末字符={stripped[-1:]!r}；附近={context!r}）"
        )
        raise WriterError("invalid_json", f"分组 Writer 输出不是合法 JSON：{diagnostic}") from error
    except (ValidationError, TypeError) as error:
        details: tuple[str, ...] = ()
        if isinstance(error, ValidationError):
            details = tuple(
                f"{'.'.join(str(part) for part in item.get('loc', ()))}：{item.get('msg', '')}"
                for item in error.errors()[:5]
            )
        message = "分组 Writer 输出不符合 answers schema"
        if details:
            message += "：" + "；".join(details)
        raise WriterError(
            "invalid_schema",
            message,
            validation_error_count=len(error.errors()) if isinstance(error, ValidationError) else 1,
            validation_details=details,
        ) from error
    ids = [item.question_id for item in answers]
    if len(ids) != len(expected) or set(ids) != set(expected):
        raise WriterError(
            "invalid_section_coverage",
            f"分组只能完整返回 {', '.join(expected)}（实际为 {', '.join(ids) or '空'}）",
        )
    answers = sorted(answers, key=lambda item: expected.index(item.question_id))
    _validate_answer_semantics(answers)
    return answers


def _normalize_answer_numbers(answers: list[Answer]) -> list[Answer]:
    """移除百分比 NumericFact 上没有含义的币种标签。

    模型常把同一张收入拆分表的 CNY 标签复制到百分比，数值和来源本身没有变化。
    这是无损的结构归一化，避免将“百分比带币种”误报成事实冲突。
    """
    normalized = [answer.model_dump(mode="json") for answer in answers]
    for answer in normalized:
        for statement in answer["statements"]:
            for number in statement["numbers"]:
                unit = str(number["unit"]).replace(" ", "").lower()
                if unit in {"%", "百分比"}:
                    number["currency"] = None
    return [Answer.model_validate(answer) for answer in normalized]


def _pack_payload(pack: Any) -> dict[str, Any]:
    """原文只出现一次；每题预留证据用 source_id/locator 索引。"""
    payload = pack.model_dump(mode="json")
    payload["question_chunks"] = {
        qid: [{"source_id": chunk.source_id, "locator": chunk.locator} for chunk in chunks]
        for qid, chunks in pack.question_chunks.items()
    }
    return payload


def _answer_messages(group_id: str, pack: Any, calculations: list[Any]) -> list[dict[str, str]]:
    qids = pack.question_ids
    system = (
        '你是固定公司研究的分组 Writer。只输出 JSON 对象 {"answers":[...]}，'
        f"且只可返回 {', '.join(qids)}，顺序不可改变。answers 不得为空，且必须恰好包含上述每个 question_id 一次。"
        "即使某题没有足够证据，也必须为该题返回一个 status=partial 或 missing 且带完整 gap 的 Answer；不得省略该题或以空数组代替。"
        "使用 ResearchDraft.Answer/Statement/Gap/NumericFact 结构。"
        "EvidencePack.question_chunks 为每题预留的相关证据；先用对应题目的预留片段，再判断证据是否足够。"
        "只能引用证据包里的 source_id 和 locator；可信计算值不可改写。没有直接证据时使用 partial/missing 和完整 gap。"
        "answered 的事实与数字必须有 supports 引用且 gap 必须为 null。EvidenceRef 必须包含 source_id、locator、relation；"
        "NumericFact 必须包含 value、unit、currency、period、basis、evidence。不得使用 quote/name 等 schema 外字段。"
        "NumericFact.value 只能是十进制数；日期、姓名、unknown/null/不适用不得放入 numbers，日期仅写在 Statement.text。"
        "所有自然语言字符串内部只能使用中文引号“”或单引号，不能直接写未转义的 ASCII 双引号；"
        "返回前必须按严格 JSON 语法检查逗号、双引号和括号配对。"
        "不得输出估值、目标价或投资动作。"
    )
    user = json.dumps(
        {
            "questions": {qid: QUESTION_TEXT[qid] for qid in qids},
            "evidence_pack": _pack_payload(pack),
            "trusted_calculations": [item.model_dump(mode="json") for item in calculations],
            "required_json_schema": AnswerSection.model_json_schema(),
        },
        ensure_ascii=False,
        default=str,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _answer_revision_messages(
    group_id: str,
    pack: Any,
    calculations: list[Any],
    current_answers: list[Answer],
    issues: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """把审查问题作为独立的最后一轮指令，避免被大证据包淹没。"""
    messages = _answer_messages(group_id, pack, calculations)
    messages[0]["content"] += (
        "\n这是一次定向修订。你必须返回该分组的完整替换答案，并逐项落实下方每一个"
        " severity=error 的 revision_instruction；不要只复述原答案，也不要跳过没有问题的题。"
        "只能改正期间、单位、口径、表述或既有证据引用；不得编造事实或引用。"
    )
    messages.append(
        {
            "role": "user",
            "content": json.dumps(
                {
                    "current_answers": [
                        answer.model_dump(mode="json") for answer in current_answers
                    ],
                    "mandatory_revision_issues": issues,
                },
                ensure_ascii=False,
                default=str,
            ),
        }
    )
    return messages


_GROUP_CALCULATION_METRICS: dict[str, frozenset[str]] = {
    # Q03 需要年度资本开支强度；Q09–Q12 才需要完整的财务质量和资本结构序列。
    "Q01-Q04": frozenset({"capex_to_revenue", "cash_capex_proxy", "cash_capex_proxy_to_revenue"}),
    "Q05-Q08": frozenset(),
    "Q09-Q12": frozenset(
        {
            "net_profit_yoy_growth",
            "roe_series",
            "capex_to_revenue",
            "cash_capex_proxy",
            "cash_capex_proxy_to_revenue",
        }
    ),
    "Q13-Q16": frozenset(),
    "Q17-Q20": frozenset(),
    "Q21-Q24": frozenset(),
}


def _calculations_for_group(group_id: str, calculations: list[Any]) -> list[Any]:
    """只把该问题组能直接解释的可信计算交给 Writer，避免无关十年序列占满上下文。"""
    allowed = _GROUP_CALCULATION_METRICS[group_id]
    return [calculation for calculation in calculations if calculation.metric in allowed]


def _synthesis_messages(
    run: dict[str, Any], answers: list[Answer], packs: dict[str, Any], calculations: list[Any]
) -> list[dict[str, str]]:
    moat_contract = "\n".join(
        f"{dimension_id} {item['name']}：{item['definition']}；优先检查 {', '.join(item['related_questions'])}。"
        for dimension_id, item in MOAT_DEFINITIONS.items()
    )
    system = (
        "你是固定公司研究的综合判断 Writer。答案已由 Python 冻结，不得返回或重写 answers。"
        f"只输出一个 JSON 对象，包含 gates({', '.join(GATE_IDS)})、moats({', '.join(MOAT_IDS)})、challenge、conflicts、conclusion、review_explanations。"
        "结构必须与 ResearchDraft 对应字段一致。门闸 pass/fail 与非空评分必须有 eligible supports 引用；证据不足用 insufficient/null。"
        "不得输出估值、目标价、交易建议或总分。"
        "为避免输出被截断，每个 Statement.text、Gap 的每个字段、challenge 的字符串字段均限 80 个中文字符；"
        "conflicts 和 review_explanations 没有必要时返回空数组。"
        "只用下列紧凑字段契约：gates 必须恰好含 G1/G2/G3/G5/G6/G7，"
        "每项为 {gate_id,result,rationale:{id,kind,text,evidence,numbers},gap}，其中 result 只能是 ASCII 的 pass/fail/insufficient；"
        "moats 必须恰好含 M1 至 M6，每项为 {dimension_id,score,confidence,rationale:{id,kind,text,evidence,numbers},gap}；"
        "score 是 0 至 10 的整数或 null，confidence 只能是 ASCII 的 low/medium/high；"
        "六个维度必须严格按以下定义逐项判断，不得按答案出现顺序重新排列，也不得把相邻机制写入错误编号：\n"
        f"{moat_contract}\n"
        f"评分锚点：{json.dumps(MOAT_SCORE_ANCHORS, ensure_ascii=False)}。"
        "同一事实可以支持多个维度，但 rationale 必须解释它如何形成该维度的机制；"
        "网络或用户规模本身只能直接支持 M4，只有同时证明单位成本随规模改善时才能支持 M5，不能支持 M1。"
        "牌照、频谱和准入只属于 M6；公司治理、管理层持股和一般研发投入不属于任何护城河维度。"
        "M1 必须有可核验的相对单位成本或成本结构证据，缺少时为 null。M2–M6 可以基于一手或已报告"
        " provider 事实作保守 inference 评分：例如多边生态与参与者规模可支持 M4，融合服务或工作流嵌入可支持 M3，"
        "规模、固定投入和资源利用率的组合可支持 M5。此时必须解释机制、降低 confidence，并保留尚缺的量化证据 gap；"
        "只有不存在任何可用事实支撑该机制时才为 null，不能仅因缺少第三方调研或精确因果估计而把可判断维度全部留空。"
        "所有 Statement 的 kind 只能是 ASCII 的 fact/inference/assumption；"
        "challenge 为 {counterargument:{id,kind,text,evidence,numbers},channels_checked,search_scope,refutation_found,overturning_evidence,next_check}；"
        "conclusion 为 {id,kind,text,evidence,numbers}。"
        "challenge.overturning_evidence 必须是非空字符串；没有直接推翻证据时填写“未发现直接推翻当前判断的证据”。"
    )
    user = json.dumps(
        {
            "ticker": run["ticker"],
            "as_of": run["as_of"],
            "answers": [answer.model_dump(mode="json") for answer in answers],
            "trusted_calculations": [item.model_dump(mode="json") for item in calculations],
        },
        ensure_ascii=False,
        default=str,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _section_repair_messages(
    unit: str,
    content: str,
    error: WriterError,
    original: list[dict[str, str]],
    *,
    expected_question_ids: tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    coverage_requirement = ""
    if expected_question_ids:
        expected_ids = ", ".join(expected_question_ids)
        coverage_requirement = (
            f"answers 必须恰好包含 {expected_ids} 各一次，绝不能返回空 answers 数组。"
            "任何证据不足的题仍须返回 status=partial 或 missing 和完整 gap；这不属于新增事实。"
        )
    if error.code == "invalid_json":
        regenerated = deepcopy(original)
        regenerated[0]["content"] += (
            f"\n上一次 {unit} 响应存在 JSON 语法错误：{error.message}。"
            "请根据同一输入重新生成一个完整 JSON 对象；不要复制上一响应，不能输出注释或 Markdown。"
            "字符串内部使用中文引号“”，不能写未转义的 ASCII 双引号；返回前检查严格 JSON 语法。"
            f"{coverage_requirement}"
        )
        return regenerated
    return [
        {
            "role": "system",
            "content": (
                f"你是 {unit} JSON schema 修复器。只修复结构、必填字段和编号覆盖问题；"
                "不得新增数字或事实。若错误涉及证据包范围或来源资格，只能改用原 EvidencePack 中已有的合格引用，"
                "或将受影响答案降为 partial/missing 并补完整 gap。"
                f"{coverage_requirement}只输出修复后的 JSON 对象。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "original_instruction": original,
                    "error": error.message,
                    "invalid_output": content,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _request_section(
    root: Any,
    tracker: dict[str, Any],
    *,
    unit: str,
    kind: str,
    messages: list[dict[str, str]],
    parse: Callable[[str], Any],
    api_key: str,
    model: str,
    temperature: float | None,
    thinking: str | None,
    expected: tuple[str, ...] | None = None,
) -> Any:
    """初写与修订共用解析、证据校验和一次修复；不增加总调用预算。"""
    for attempt in range(2):
        response = _tracked_request(
            root,
            tracker,
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=temperature if attempt == 0 else 0.0,
            thinking=thinking if attempt == 0 else "disabled",
            kind=kind if attempt == 0 else f"schema_repair:{unit}",
        )
        content = _visible_response_content(response) or ""
        try:
            if _finish_reason(response) == "length":
                raise WriterError(
                    "output_truncated", f"{unit} 输出达到 token 上限，不能作为完整分组保存"
                )
            return parse(_extract_content(response))
        except (json.JSONDecodeError, TypeError, WriterError) as error:
            issue = (
                error
                if isinstance(error, WriterError)
                else WriterError("invalid_json", f"{unit} 不是合法 JSON 对象")
            )
            tracker["attempts"][-1]["validation_code"] = issue.code
            if attempt:
                raise WriterError(issue.code, f"{unit}：{issue.message}") from error
            messages = _section_repair_messages(
                unit,
                content,
                issue,
                messages,
                expected_question_ids=expected,
            )
    raise AssertionError("unreachable")


def _parse_answer_section(
    content: str, expected: tuple[str, ...], pack: Any, calculations: list[Any]
) -> list[Answer]:
    answers = _parse_answers(content, expected)
    _validate_answer_evidence(answers, pack, calculations)
    return answers


def _parse_synthesis_section(
    content: str,
    run: dict[str, Any],
    answers: list[Answer],
    packs: dict[str, Any],
    calculations: list[Any],
) -> dict[str, Any]:
    synthesis = _normalize_synthesis(_parse_synthesis(content))
    _validate_synthesis_evidence(_assemble_draft(run, answers, synthesis), packs, calculations)
    return synthesis


def _validation_revision_unit(
    issue: research.ValidationIssue,
    draft: ResearchDraft,
) -> str | None:
    """把可由 Writer 修订的确定性校验错误映射回问题组或综合单元。"""
    answer_match = re.search(r"draft\.answers\[(\d+)\]", issue.location)
    if answer_match:
        index = int(answer_match.group(1))
        if index < len(draft.answers):
            question_id = draft.answers[index].question_id
            return next(
                (
                    group
                    for group, question_ids in QUESTION_GROUPS.items()
                    if question_id in question_ids
                ),
                None,
            )
    if issue.location.startswith(
        ("draft.gates", "draft.moats", "draft.challenge", "draft.conclusion", "draft.conflicts")
    ):
        return "synthesis"
    return None


def _assemble_draft(
    run: dict[str, Any], answers: list[Answer], synthesis: dict[str, Any]
) -> ResearchDraft:
    allowed = {
        key: synthesis[key]
        for key in ("gates", "moats", "challenge", "conflicts", "conclusion", "review_explanations")
        if key in synthesis
    }
    payload = {
        "schema_version": 1,
        "ticker": run["ticker"],
        "as_of": run["as_of"],
        "answers": [item.model_dump(mode="json") for item in answers],
        **allowed,
    }
    return _validate_draft(payload)


def _parse_synthesis(content: str) -> dict[str, Any]:
    try:
        value = _json_object(content)
    except json.JSONDecodeError:
        objects = _json_objects(content)
        if not objects:
            raise
        combined: dict[str, Any] = {}
        for item in objects:
            combined.update(item)
        value = combined
    for wrapper in ("synthesis", "draft", "result"):
        nested = value.get(wrapper)
        if isinstance(nested, dict):
            value = nested
            break
    return value


def _normalize_synthesis(synthesis: dict[str, Any]) -> dict[str, Any]:
    """只归一化可无损识别的枚举、ID 映射和空缺文本。"""
    normalized = deepcopy(synthesis)
    for field, id_field, expected_ids in (
        ("gates", "gate_id", GATE_IDS),
        ("moats", "dimension_id", MOAT_IDS),
    ):
        values = normalized.get(field)
        if isinstance(values, dict) and set(values) == set(expected_ids):
            normalized[field] = [
                {id_field: identifier, **value}
                if isinstance(value, dict) and id_field not in value
                else value
                for identifier, value in (
                    (identifier, values[identifier]) for identifier in expected_ids
                )
            ]
    challenge = normalized.get("challenge")
    if isinstance(challenge, dict) and not str(challenge.get("overturning_evidence") or "").strip():
        challenge["overturning_evidence"] = "模型未提供，需人工复核"
    for moat in normalized.get("moats", []):
        if not isinstance(moat, dict):
            continue
        confidence = moat.get("confidence")
        moat["confidence"] = {"低": "low", "中": "medium", "高": "high"}.get(confidence, confidence)
    return normalized


def _review_messages(payload: dict[str, Any], *, compact: bool = False) -> list[dict[str, str]]:
    system = (
        "你是固定研究语义审查器。只检查题意对齐、证据支持、数值一致、跨章节一致和范围违规。"
        "只输出 DraftReview JSON，不修改草稿，不输出推理过程。checked_questions/gates/moats 必须逐项完整列出。"
        "issue.category 只能是 question_alignment/evidence_support/numeric_consistency/cross_section_consistency/scope_violation；"
        "severity 为 error/warning；每项包含 target/message/evidence/revision_instruction。存在 error 时 status=failed，否则 passed。"
        "只有必须修改草稿的实质缺陷才能标 error；若数值一致、只是建议补充说明或修订指令为“无需修改”，必须标 warning。"
        "当 Answer.status 为 partial/missing 且有完整 gap 时，已明确回答证据不足；不得仅因 statements 为空而判 question_alignment error。"
        "合理四舍五入不构成 numeric_consistency error。"
        "Statement.text 之外的 NumericFact 也是草稿正文的一部分：每个 NumericFact 已保存数值、期间、口径和逐项 evidence。"
        "不得因为数值或其来源没有在自然语言句子中重复书写而报 error；应检查对应 NumericFact.evidence。"
        "可信计算的 NumericFact.evidence 已覆盖计算输入来源时，不得要求在同一 Statement.text 重复列出分子、分母或全部绝对额。"
        "PDF 的 page N 是有效的精确 locator；不得仅因它不是 provider 的 item_list locator 而报 evidence_support error。"
        "引用只能来自输入中的 cited_evidence。"
        "最多返回 20 项 issues；每项 message 与 revision_instruction 均不得超过 100 个中文字符。"
    )
    if compact:
        system += (
            "上次审查输出无法解析。请重新独立审查，保持所有字段紧凑，并只输出单个完整 JSON 对象。"
        )
    payload = {**payload, "required_json_schema": DraftReview.model_json_schema()}
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


def _parse_review(content: str, draft: ResearchDraft, packs: dict[str, Any]) -> DraftReview:
    raw = _json_object(content)
    raw["draft_hash"] = draft_hash(draft)
    if isinstance(raw.get("issues"), list):
        raw["status"] = (
            "failed"
            if any(
                isinstance(item, dict) and item.get("severity") == "error" for item in raw["issues"]
            )
            else "passed"
        )
    return _normalize_review(DraftReview.model_validate(raw), draft, packs)


def _normalize_review(
    review: DraftReview, draft: ResearchDraft, evidence_packs: dict[str, Any] | None = None
) -> DraftReview:
    """纠正 Reviewer 可由 Python 识别的自相矛盾严重度，不改写问题内容。"""
    answer_status = {answer.question_id: answer.status for answer in draft.answers}
    answer_has_gap = {answer.question_id: answer.gap is not None for answer in draft.answers}
    evidence_text: dict[tuple[str, str], str] = {}
    for pack in (evidence_packs or {}).values():
        for chunk in pack.chunks:
            evidence_text[(chunk.source_id, chunk.locator)] = json.dumps(
                chunk.model_dump(mode="json"), ensure_ascii=False, default=str
            )
    answers = {answer.question_id: answer for answer in draft.answers}
    payload = review.model_dump(mode="json")
    for item in payload["issues"]:
        if item["severity"] != "error":
            continue
        instruction = item["revision_instruction"]
        message = item["message"]
        qid = next((value for value in QUESTION_IDS if value in item["target"]), None)
        correctness_markers = (
            "数值一致",
            "与披露一致",
            "与可信计算一致",
            "换算一致",
            "换算正确",
            "数值无误",
            "符合披露",
            "均正确",
            "均一致",
        )
        contradiction_markers = ("不一致", "不正确", "不匹配", "不符合", "有误", "错误", "不相符")
        expected_rounded = re.findall(r"四舍五入应为\s*([0-9]+(?:\.[0-9]+)?%?)", message)
        target_answer = answers.get(qid) if qid is not None else None
        target_statement_id = None
        if qid is not None:
            match = re.search(rf"{re.escape(qid)}-(S\d+)", item["target"])
            target_statement_id = match.group(1) if match else None
        target_statement = next(
            (
                statement
                for statement in (target_answer.statements if target_answer else [])
                if statement.id in {item["target"], target_statement_id}
            ),
            None,
        )
        answer_fragments = (
            [
                item
                for statement in target_answer.statements
                for item in (statement.text, *(number.period for number in statement.numbers))
            ]
            if target_answer
            else []
        )
        answer_text = " ".join(answer_fragments)
        already_matches_rounding = bool(expected_rounded) and any(
            value in answer_text for value in expected_rounded
        )
        mentioned_years = {int(value) for value in re.findall(r"20\d{2}", message)}
        for start, end in re.findall(r"(20\d{2})\s*(?:至|到|[-–—])\s*(20\d{2})", message):
            mentioned_years.update(range(int(start), int(end) + 1))
        reports_missing_years = "未列" in message or "未逐项列" in message
        already_lists_mentioned_years = (
            reports_missing_years
            and bool(mentioned_years)
            and all(str(year) in answer_text for year in mentioned_years)
        )
        self_contradictory = (
            "无需修改" in instruction
            or "无实质冲突" in message
            or already_matches_rounding
            or already_lists_mentioned_years
            or (
                any(marker in message for marker in correctness_markers)
                and not any(marker in message for marker in contradiction_markers)
            )
        )
        accepted_gap = (
            item["category"] == "question_alignment"
            and qid is not None
            and answer_status.get(qid) in {"partial", "missing"}
            and answer_has_gap.get(qid) is True
        )
        numeric_field_false_negative = False
        if (
            qid is not None
            and item["category"] == "evidence_support"
            and "字段" in message
            and ("未含" in message or "不匹配" in message)
        ):
            numbers = [
                number for statement in answers[qid].statements for number in statement.numbers
            ]
            numeric_field_false_negative = bool(numbers) and all(
                any(
                    (
                        str(number.value)
                        in evidence_text.get((source_id_text(ref.source_id), ref.locator), "")
                        or str(float(number.value))
                        in evidence_text.get((source_id_text(ref.source_id), ref.locator), "")
                    )
                    for ref in number.evidence
                )
                for number in numbers
            )
        # Reviewer 经常只读自然语言句子，忽略紧随其后的 NumericFact。对指定
        # Statement，其 numbers 已逐项带有 supports 引用时，不能再以“未逐项
        # 引用”“只有 page locator”或“没有在文本重列原始输入”为由报 error。
        statement_numeric_evidence = (
            target_statement is not None
            and bool(target_statement.numbers)
            and all(
                any(reference.relation == "supports" for reference in number.evidence)
                for number in target_statement.numbers
            )
        )
        asks_for_repeated_numeric_trace = item["category"] in {
            "evidence_support",
            "numeric_consistency",
        } and any(
            marker in message
            for marker in (
                "未逐项",
                "未列示",
                "未在S",
                "未在 S",
                "数值链不完整",
                "仅引用page",
                "仅引用 page",
                "精确引用位置",
                "分母收入",
                "绝对额",
            )
        )
        trusted_numeric_trace_available = (
            statement_numeric_evidence
            and target_statement is not None
            and "可信计算" in target_statement.text
            and any(marker in message for marker in ("分母", "绝对额", "数值链", "未列示"))
        )
        locator_false_negative = (
            statement_numeric_evidence
            and item["category"]
            in {
                "evidence_support",
                "numeric_consistency",
                "cross_section_consistency",
            }
            and asks_for_repeated_numeric_trace
        )
        if (
            self_contradictory
            or accepted_gap
            or numeric_field_false_negative
            or trusted_numeric_trace_available
            or locator_false_negative
        ):
            item["severity"] = "warning"
    payload["status"] = (
        "failed" if any(item["severity"] == "error" for item in payload["issues"]) else "passed"
    )
    return DraftReview.model_validate(payload)


def _verify_numeric_review(
    review: DraftReview, draft: ResearchDraft, calculations: list[Any], inputs: list[Any]
) -> DraftReview:
    """只纠正可由归档数值证明的误报；不以模型自身的保证替代核对。"""
    payload = review.model_dump(mode="json")
    answers = {answer.question_id: answer for answer in draft.answers}
    roe = next((item for item in calculations if item.metric == "roe_series"), None)

    def supported(number: Any) -> bool:
        return any(
            abs(number.value - item.value) <= Decimal("1e-8")
            and number.period == item.period
            and (
                number.basis == item.label
                or (number.basis == "报告 ROE" and item.label == "净资产收益率（ROE）")
            )
            and any(
                source_id_text(ref.source_id) == item.source_id
                and (ref.locator == item.locator or item.locator.startswith(ref.locator + "."))
                for ref in number.evidence
                if ref.relation == "supports"
            )
            for item in inputs
        )

    for issue in payload["issues"]:
        if issue["severity"] != "error" or issue["category"] != "numeric_consistency":
            continue
        match = re.fullmatch(r"(Q\d{2})-(S\d+)", issue["target"])
        if not match or match[1] not in answers:
            continue
        answer = answers[match[1]]
        statement = next(
            (s for s in answer.statements if s.id in {issue["target"], match[2]}), None
        )
        if statement is None:
            continue
        message = issue["message"]
        proof = None
        if (
            match[1] == "Q09"
            and roe is not None
            and "15%" in message
            and any(marker in message for marker in ("财年数", "计数", "达标年份"))
        ):
            periods = [row["period"] for row in roe.values]
            count = sum(Decimal(str(row["value"])) > 15 for row in roe.values)
            counts = [n for n in statement.numbers if n.basis == "ROE 高于 15% 的财年数"]
            if counts and periods:
                span = f"{min(periods)}-{max(periods)}"
                if all(n.value == count and n.period == span for n in counts) and re.search(
                    rf"{count}\s*个财年\s*ROE\s*高于\s*15%", statement.text
                ):
                    proof = f"Python 核对完整 ROE 序列：高于 15% 的财年数为 {count}，草稿计数正确。"
        if "精度" in message and "应为" in message:
            expected = re.search(r"(20\d{2}/FY)\s*ROE应为\s*(-?\d+(?:\.\d+)?)%", message)
            if expected:
                numbers = [
                    n
                    for n in statement.numbers
                    if n.period == expected[1] and n.unit == "%" and n.basis == "报告 ROE"
                ]
                if numbers and all(
                    n.value == Decimal(expected[2]) and supported(n) for n in numbers
                ):
                    proof = "Python Decimal 与归档字段核对一致；尾随零仅影响展示精度。"
        if match[1] == "Q12" and "四舍五入可接受" in message and "口径" in message:
            for ratio in statement.numbers:
                labels = ("短期借款与融资租赁负债", "长期借款与租赁负债", "股东权益合计")
                operands = [
                    next(
                        (
                            n
                            for s in answer.statements
                            for n in s.numbers
                            if n.period == ratio.period and n.basis == label and supported(n)
                        ),
                        None,
                    )
                    for label in labels
                ]
                if all(n is not None for n in operands):
                    short, long, equity = operands
                    if (
                        equity.value > 0
                        and len({n.currency for n in operands}) == 1
                        and len({n.unit for n in operands}) == 1
                        and all(
                            label in ratio.basis and label in statement.text for label in labels
                        )
                        and "除以" in statement.text
                        and ((short.value + long.value) / equity.value).quantize(Decimal("0.01"))
                        == ratio.value
                        and re.search(
                            rf"债务权益比约\s*{re.escape(str(ratio.value))}(?!\d)", statement.text
                        )
                    ):
                        proof = "Python 核对归档债务及权益字段：比值四舍五入与草稿一致，正文和 NumericFact 已标注口径。"
        if proof:
            issue["severity"] = "warning"
            issue["message"] = f"{message}【离线核对：{proof}】"
    payload["status"] = (
        "failed" if any(i["severity"] == "error" for i in payload["issues"]) else "passed"
    )
    return DraftReview.model_validate(payload)


def _append_trusted_numeric_review_issues(
    review: DraftReview, draft: ResearchDraft, calculations: list[Any]
) -> DraftReview:
    """用 Python 已计算的序列阻止模型把非单调序列描述为“逐步下降”。"""
    capex_ratio = next((item for item in calculations if item.metric == "capex_to_revenue"), None)
    if capex_ratio is None:
        return review
    try:
        values = [Decimal(str(item["value"])) for item in capex_ratio.values]
    except (KeyError, ValueError, ArithmeticError):
        return review
    if not any(current > previous for previous, current in pairwise(values)):
        return review
    answer = next((item for item in draft.answers if item.question_id == "Q03"), None)
    answer_text = " ".join(statement.text for statement in answer.statements) if answer else ""
    monotonic_claims = ("逐步降", "逐年下降", "连续下降", "逐年回落")
    if not any(claim in answer_text for claim in monotonic_claims):
        return review
    if any(item.target == "Q03-trusted-capex-trend" for item in review.issues):
        return review
    payload = review.model_dump(mode="json")
    payload["issues"].append(
        ReviewIssue(
            category="numeric_consistency",
            target="Q03-trusted-capex-trend",
            severity="error",
            message="可信资本开支/收入序列存在年度回升，不能写成逐步或逐年下降。",
            evidence=capex_ratio.source_refs[:5],
            revision_instruction="改为“总体下降，期间存在小幅回升”，并保留各年度数值。",
        ).model_dump(mode="json")
    )
    payload["status"] = "failed"
    return DraftReview.model_validate(payload)


def _review_draft(
    root: Any,
    run_id: str,
    *,
    provider: str = "deepseek",
    model: str | None = None,
    temperature: float | None = None,
    thinking: str | None = None,
    review: DraftReview | None = None,
    reviewer_type: str = "model",
    tracker: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[research.ValidationIssue]]:
    """运行语义审查，或导入符合相同契约的人工审查。"""
    from wonder_stockresearch.fixed.fixed_quality import (
        REVIEW_TEMPLATE_VERSION,
        build_evidence_packs,
        review_prompt_payload,
    )

    run, sources = _load_run_sources(root, run_id)
    if not run.get("draft_json"):
        raise research.ResearchError("empty_draft", "该 run 还没有保存草稿", exit_code=2)
    draft = ResearchDraft.model_validate_json(run["draft_json"])
    resolved_model = _resolved_model(model)
    calculations: list[Any] = []
    if review is None:
        envfile.load_env_file(root / ".env")
        local_tracker = tracker or {"calls": 0, "attempts": [], "usage": {}}
        packs = build_evidence_packs(root, sources, draft.as_of)
        calculations = calculate_trusted_metrics(root, sources)
        review_payload = review_prompt_payload(draft, packs, calculations)
        response = _tracked_request(
            root,
            local_tracker,
            api_key=_api_key(),
            model=resolved_model,
            messages=_review_messages(review_payload),
            temperature=_resolve_temperature(temperature),
            thinking=_resolve_thinking(thinking),
            kind="semantic_review",
        )
        try:
            review = _parse_review(_extract_content(response), draft, packs)
        except (json.JSONDecodeError, ValidationError, TypeError):
            retry = _tracked_request(
                root,
                local_tracker,
                api_key=_api_key(),
                model=resolved_model,
                messages=_review_messages(review_payload, compact=True),
                temperature=0.0,
                thinking="disabled",
                kind="schema_repair:semantic_review",
            )
            try:
                review = _parse_review(_extract_content(retry), draft, packs)
            except (json.JSONDecodeError, ValidationError, TypeError) as repair_error:
                details = ""
                if isinstance(repair_error, ValidationError):
                    details = "：" + "；".join(
                        f"{'.'.join(str(part) for part in item.get('loc', ()))}：{item.get('msg', '')}"
                        for item in repair_error.errors()[:5]
                    )
                elif isinstance(repair_error, json.JSONDecodeError):
                    details = f"：{repair_error.msg}（位置 {repair_error.pos}）"
                raise WriterError(
                    "invalid_review_schema", f"Reviewer 输出不符合 DraftReview schema{details}"
                ) from repair_error
        review = _append_trusted_numeric_review_issues(review, draft, calculations)
        review = _verify_numeric_review(review, draft, calculations, numeric_inputs(sources, root))
    return research.save_draft_review(
        root,
        run_id,
        review,
        reviewer_type=reviewer_type,
        provider=provider if reviewer_type == "model" else None,
        model=resolved_model if reviewer_type == "model" else None,
        template_version=REVIEW_TEMPLATE_VERSION,
    )


def recheck_draft_review(
    root: Path, run_id: str
) -> tuple[dict[str, Any], list[research.ValidationIssue]]:
    """离线复核同一草稿的既有模型审查，追加记录且不调用模型、不定稿。"""
    from wonder_stockresearch.fixed.fixed_quality import (
        build_evidence_packs,
    )

    run, sources = _load_run_sources(root, run_id)
    if not run.get("draft_json"):
        raise research.ResearchError("empty_draft", "该 run 还没有保存草稿", exit_code=2)
    draft = ResearchDraft.model_validate_json(run["draft_json"])
    with storage.open_db(root) as connection:
        previous = storage.latest_draft_review(connection, run_id)
    if (
        not previous
        or previous["reviewer_type"] != "model"
        or previous["draft_hash"] != draft_hash(draft)
    ):
        raise research.ResearchError(
            "review_recheck_unavailable",
            "需要同一草稿的既有模型审查，不能复用过期或人工审查",
            exit_code=2,
        )
    review = DraftReview.model_validate_json(previous["review_json"])
    calculations = calculate_trusted_metrics(root, sources)
    review = _normalize_review(review, draft, build_evidence_packs(root, sources, draft.as_of))
    review = _verify_numeric_review(review, draft, calculations, numeric_inputs(sources, root))
    review = _append_trusted_numeric_review_issues(review, draft, calculations)
    return research.save_draft_review(
        root,
        run_id,
        review,
        reviewer_type="model",
        provider=previous["provider"],
        model=previous["model"],
        template_version="semantic-review-v2+offline-numeric-v1",
    )


def review_draft(
    root: Any,
    run_id: str,
    *,
    provider: str = "deepseek",
    model: str | None = None,
    temperature: float | None = None,
    thinking: str | None = None,
    review: DraftReview | None = None,
    reviewer_type: str = "model",
    tracker: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[research.ValidationIssue]]:
    """审查草稿，并在直接调用时创建一条可读的 research trace。"""
    with observability.operation(
        Path(root),
        "review_draft",
        trace_kind="research",
        run_id=run_id,
        attributes={
            "provider": provider,
            "model": model or DEFAULT_MODEL,
            "reviewer_type": reviewer_type,
        },
    ):
        return _review_draft(
            root,
            run_id,
            provider=provider,
            model=model,
            temperature=temperature,
            thinking=thinking,
            review=review,
            reviewer_type=reviewer_type,
            tracker=tracker,
        )


def _write_staged(
    root: Any,
    run_id: str,
    *,
    provider: str,
    model: str | None,
    temperature: float | None,
    thinking: str | None,
    on_progress: progress.ProgressCallback | None,
) -> tuple[dict[str, Any], list[research.ValidationIssue]]:
    from wonder_stockresearch.fixed.fixed_quality import (
        build_evidence_packs,
        canonical_hash,
    )

    envfile.load_env_file(root / ".env")
    api_key = _api_key()
    resolved_model = _resolved_model(model)
    resolved_temperature = _resolve_temperature(temperature)
    resolved_thinking = _resolve_thinking(thinking)
    run, sources = _load_run_sources(root, run_id)
    with observability.span(
        Path(root),
        "skills.load",
        span_kind="chain",
        attributes={"execution": "writer"},
    ) as skill_span:
        skill_context, skill_audit = _selected_skill_context(root, run_id)
        skill_span.set_attributes(observability.skill_audit_summary(skill_audit))
    packs = build_evidence_packs(root, sources, storage.parse_iso(run["as_of"]))
    calculations = calculate_trusted_metrics(root, sources)
    tracker: dict[str, Any] = {"calls": 0, "attempts": [], "usage": {}}
    overall_hash = canonical_hash(
        {
            "packs": packs,
            "calculations": calculations,
            "skills": skill_audit,
            "version": STAGED_TEMPLATE_VERSION,
        }
    )
    attempt = research._begin_step(
        root, run_id, "write_draft", provider, overall_hash, model=resolved_model
    )
    reused: list[str] = []
    answers: list[Answer] = []
    section_hashes: dict[str, str] = {}
    try:
        for group_id, expected in QUESTION_GROUPS.items():
            progress.emit(
                on_progress, "write-draft", "running", f"正在写作 {group_id}", run_id=run_id
            )
            group_calculations = _calculations_for_group(group_id, calculations)
            answer_messages = _with_skill_context(
                _answer_messages(group_id, packs[group_id], group_calculations), skill_context
            )
            section_hash = canonical_hash(
                {"messages": answer_messages, "version": STAGED_TEMPLATE_VERSION}
            )
            section_hashes[group_id] = section_hash
            cached = _section_get(root, run_id, group_id, section_hash)
            if cached is not None:
                try:
                    group_answers = _parse_answer_section(
                        json.dumps(cached), expected, packs[group_id], group_calculations
                    )
                except WriterError:
                    cached = None
                else:
                    reused.append(group_id)
            if cached is None:
                group_answers = _request_section(
                    root,
                    tracker,
                    unit=group_id,
                    kind=f"answers:{group_id}",
                    messages=answer_messages,
                    parse=partial(
                        _parse_answer_section,
                        expected=expected,
                        pack=packs[group_id],
                        calculations=group_calculations,
                    ),
                    api_key=api_key,
                    model=resolved_model,
                    temperature=resolved_temperature,
                    thinking=resolved_thinking,
                    expected=expected,
                )
                _section_save(
                    root,
                    run_id,
                    group_id,
                    section_hash,
                    {"answers": [item.model_dump(mode="json") for item in group_answers]},
                    provider,
                    resolved_model,
                )
            answers.extend(group_answers)
        progress.emit(
            on_progress, "write-draft", "running", "正在综合门闸、评分与反证", run_id=run_id
        )
        synthesis_messages = _with_skill_context(
            _synthesis_messages(run, answers, packs, calculations), skill_context
        )
        synthesis_hash = canonical_hash(
            {"messages": synthesis_messages, "version": STAGED_TEMPLATE_VERSION}
        )
        synthesis = _section_get(root, run_id, "synthesis", synthesis_hash)
        if synthesis is not None:
            try:
                synthesis = _parse_synthesis_section(
                    json.dumps(synthesis), run, answers, packs, calculations
                )
            except (WriterError, TypeError, json.JSONDecodeError):
                synthesis = None
        if synthesis is None:
            synthesis = _request_section(
                root,
                tracker,
                unit="synthesis",
                kind="synthesis",
                messages=synthesis_messages,
                parse=partial(
                    _parse_synthesis_section,
                    run=run,
                    answers=answers,
                    packs=packs,
                    calculations=calculations,
                ),
                api_key=api_key,
                model=resolved_model,
                temperature=resolved_temperature,
                thinking=resolved_thinking,
            )
            _section_save(
                root, run_id, "synthesis", synthesis_hash, synthesis, provider, resolved_model
            )
        else:
            reused.append("synthesis")
        draft = _assemble_draft(run, answers, synthesis)
        _validate_synthesis_evidence(draft, packs, calculations)
        research.save_draft(root, run_id, ResearchPacket(draft=draft, sources=[]))
        progress.emit(
            on_progress, "review-draft", "running", "正在审查草稿与引用证据", run_id=run_id
        )
        _, review_issues = review_draft(
            root,
            run_id,
            provider=provider,
            model=resolved_model,
            temperature=resolved_temperature,
            thinking=resolved_thinking,
            tracker=tracker,
        )
        revisions = 0
        with storage.open_db(root) as connection:
            row = storage.latest_draft_review(connection, run_id)
        assert row is not None
        current_review = DraftReview.model_validate_json(row["review_json"])
        unit_issues: dict[str, list[dict[str, Any]]] = {}
        for issue in current_review.issues:
            if issue.severity != "error":
                continue
            qmatch = next((qid for qid in QUESTION_IDS if qid in issue.target), None)
            unit = next(
                (key for key, values in QUESTION_GROUPS.items() if qmatch in values),
                "synthesis",
            )
            unit_issues.setdefault(unit, []).append(issue.model_dump(mode="json"))
        for issue in research.validate_run(root, run_id):
            if issue.severity != "error" or issue.code.startswith("semantic_review_"):
                continue
            unit = _validation_revision_unit(issue, draft)
            if unit is None:
                continue
            instruction = "修正该程序硬校验错误；证据不足时降级状态并补完整 gap。"
            if issue.code == "missing_authoritative_support":
                instruction = (
                    "目标 Statement 缺少 eligible supports。若证据包没有直接支持，不得继续保留 answered；"
                    "应删除该无支持陈述，或把整个 Answer 降为 partial/missing 并补完整 gap。"
                    "不得把仅作 context 的引用改成 supports。"
                )
            unit_issues.setdefault(unit, []).append(
                {
                    "category": "evidence_support",
                    "target": issue.location,
                    "severity": "error",
                    "message": issue.message,
                    "evidence": [],
                    "revision_instruction": instruction,
                }
            )
        if unit_issues and len(unit_issues) <= MAX_REVISION_UNITS:
            for unit in sorted(unit_issues):
                progress.emit(
                    on_progress,
                    "write-draft",
                    "running",
                    f"正在按审查意见修订 {unit}",
                    run_id=run_id,
                )
                relevant = unit_issues[unit]
                if unit == "synthesis":
                    messages = _with_skill_context(
                        _synthesis_messages(run, answers, packs, calculations), skill_context
                    )
                    messages[1]["content"] += "\n定向修订问题：" + json.dumps(
                        relevant,
                        ensure_ascii=False,
                    )
                    synthesis = _request_section(
                        root,
                        tracker,
                        unit=unit,
                        kind="revision:synthesis",
                        messages=messages,
                        parse=partial(
                            _parse_synthesis_section,
                            run=run,
                            answers=answers,
                            packs=packs,
                            calculations=calculations,
                        ),
                        api_key=api_key,
                        model=resolved_model,
                        temperature=resolved_temperature,
                        thinking=resolved_thinking,
                    )
                else:
                    expected = QUESTION_GROUPS[unit]
                    unit_calculations = _calculations_for_group(unit, calculations)
                    messages = _with_skill_context(
                        _answer_revision_messages(
                            unit,
                            packs[unit],
                            unit_calculations,
                            [answer for answer in answers if answer.question_id in expected],
                            relevant,
                        ),
                        skill_context,
                    )
                    revised = _request_section(
                        root,
                        tracker,
                        unit=unit,
                        kind=f"revision:{unit}",
                        messages=messages,
                        parse=partial(
                            _parse_answer_section,
                            expected=expected,
                            pack=packs[unit],
                            calculations=unit_calculations,
                        ),
                        api_key=api_key,
                        model=resolved_model,
                        temperature=resolved_temperature,
                        thinking=resolved_thinking,
                        expected=expected,
                    )
                    answers = [answer for answer in answers if answer.question_id not in expected]
                    answers.extend(revised)
                    answers.sort(key=lambda item: QUESTION_IDS.index(item.question_id))
                    _section_save(
                        root,
                        run_id,
                        unit,
                        section_hashes[unit],
                        {"answers": [item.model_dump(mode="json") for item in revised]},
                        provider,
                        resolved_model,
                    )
                revisions += 1
                draft = _assemble_draft(run, answers, synthesis)
                _validate_synthesis_evidence(draft, packs, calculations)
                research.save_draft(root, run_id, ResearchPacket(draft=draft, sources=[]))
            draft = _assemble_draft(run, answers, synthesis)
            research.save_draft(root, run_id, ResearchPacket(draft=draft, sources=[]))
            progress.emit(
                on_progress, "review-draft", "running", "正在复核修订后的完整草稿", run_id=run_id
            )
            _, review_issues = review_draft(
                root,
                run_id,
                provider=provider,
                model=resolved_model,
                temperature=resolved_temperature,
                thinking=resolved_thinking,
                tracker=tracker,
            )
        issues = research.validate_run(root, run_id)
        if not issues and revisions:
            revised_synthesis_messages = _with_skill_context(
                _synthesis_messages(run, answers, packs, calculations), skill_context
            )
            revised_hash = canonical_hash(
                {
                    "messages": revised_synthesis_messages,
                    "version": STAGED_TEMPLATE_VERSION,
                }
            )
            _section_save(
                root,
                run_id,
                "synthesis",
                revised_hash,
                synthesis,
                provider,
                resolved_model,
            )
        metadata = {
            "template_version": STAGED_TEMPLATE_VERSION,
            "strategy": "staged",
            "model_calls": tracker["calls"],
            "model_attempts": tracker["attempts"],
            "usage": tracker["usage"],
            "reused_sections": reused,
            "revisions": revisions,
            "review_issue_count": len(review_issues),
            "validation_issue_count": len(issues),
            "skills": skill_audit,
        }
        research._end_step(
            root,
            run_id,
            "write_draft",
            attempt,
            "succeeded",
            output_ref=f"draft:{run_id}",
            metadata=metadata,
        )
        return {
            "run_id": run_id,
            "status": "draft",
            "strategy": "staged",
            "provider": provider,
            "model": resolved_model,
            "input_hash": overall_hash,
            "template_version": STAGED_TEMPLATE_VERSION,
            "model_calls": tracker["calls"],
            "usage": tracker["usage"],
            "reused_sections": reused,
            "revisions": revisions,
            "issue_count": len(issues),
        }, issues
    except Exception as error:
        research._end_step(
            root,
            run_id,
            "write_draft",
            attempt,
            "failed",
            error=_error_payload(error),
            metadata={
                "template_version": STAGED_TEMPLATE_VERSION,
                "strategy": "staged",
                "model_calls": tracker["calls"],
                "model_attempts": tracker["attempts"],
                "usage": tracker["usage"],
                "skills": skill_audit,
            },
        )
        raise


def write_draft(
    root: Any,
    run_id: str,
    *,
    provider: str = "deepseek",
    model: str | None = None,
    temperature: float | None = None,
    thinking: str | None = None,
    strategy: str = "staged",
    on_progress: progress.ProgressCallback | None = None,
) -> tuple[dict[str, Any], list[research.ValidationIssue]]:
    """默认分组写作；one-shot 仅用于回归比较且同样经过语义审查。"""
    if strategy not in {"staged", "one-shot"}:
        raise WriterError("invalid_strategy", "strategy 只能是 staged 或 one-shot", exit_code=2)
    with observability.operation(
        Path(root),
        "write_draft",
        trace_kind="research",
        run_id=run_id,
        attributes={"provider": provider, "model": model or DEFAULT_MODEL, "strategy": strategy},
    ):
        if strategy == "staged":
            return _write_staged(
                root,
                run_id,
                provider=provider,
                model=model,
                temperature=temperature,
                thinking=thinking,
                on_progress=on_progress,
            )
        data, _ = _write_one_shot(
            root,
            run_id,
            provider=provider,
            model=model,
            temperature=temperature,
            thinking=thinking,
            on_progress=on_progress,
        )
        preliminary = research.validate_run(root, run_id)
        needs_review = any(issue.code == "semantic_review_missing" for issue in preliminary)
        review_status = "not_required"
        if needs_review:
            review_data, _ = review_draft(
                root,
                run_id,
                provider=provider,
                model=model,
                temperature=temperature,
                thinking=thinking,
            )
            review_status = str(review_data["review_status"])
        issues = research.validate_run(root, run_id)
        return {
            **data,
            "strategy": "one-shot",
            "review_status": review_status,
            "issue_count": len(issues),
        }, issues


def _error_payload(error: BaseException) -> dict[str, Any]:
    return {"code": getattr(error, "code", type(error).__name__), "message": str(error)}
