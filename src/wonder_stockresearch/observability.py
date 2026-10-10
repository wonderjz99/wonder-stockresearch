"""安全、可移植的 LLM 调用轨迹。

本模块的本地 SQLite 轨迹始终可用；设置 ``WONDER_TRACE_EXPORTER=otlp`` 后，
同一组 span 会以 OpenTelemetry OTLP/HTTP 导出。导出是可选的：缺少额外依赖或
导出端不可用都不会改变研究或 Agent 的业务结果。

轨迹仅保存名称、耗时、模型/调用种类、token 用量和内容长度/哈希等元数据。绝不
保存凭据、完整 prompt、完整模型输出、完整工具结果或隐藏推理。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from base64 import b64encode
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from . import storage

_SENSITIVE_ATTRIBUTE_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "secret",
    "password",
    "prompt",
    "message",
    "content",
    "response",
    "output",
    "result",
    "body",
    "text",
)
_MAX_ATTRIBUTE_TEXT = 256


@dataclass
class _OtelBridge:
    """仅在用户显式启用 OTLP 时懒加载 OpenTelemetry SDK。"""

    tracer: Any
    provider: Any
    trace_api: Any
    context_api: Any


@dataclass
class TraceContext:
    root: Path
    trace_id: str
    trace_kind: str
    run_id: str | None
    session_id: str | None
    turn_id: str | None
    attempt_id: str | None
    attributes: dict[str, object]
    otel_span: Any | None = None
    otel_token: Any | None = None


@dataclass
class ActiveSpan:
    root: Path
    trace_id: str
    span_id: str
    attributes: dict[str, object] = field(default_factory=dict)
    # 只交给显式配置的 OTLP 后端，不写入 SQLite 的原文属性。
    export_attributes: dict[str, object] = field(default_factory=dict)
    otel_span: Any | None = None
    otel_token: Any | None = None
    _started: float = field(default_factory=perf_counter)

    def set_attribute(self, key: str, value: object) -> None:
        """只接收可安全持久化的标量摘要；敏感字段会被丢弃。"""
        self.attributes.update(_safe_attributes({key: value}))

    def set_attributes(self, values: Mapping[str, object]) -> None:
        self.attributes.update(_safe_attributes(values))

    def set_export_attributes(self, values: Mapping[str, object]) -> None:
        """保存仅供 OTLP 导出的、已由本模块显式准备的可见调试字段。

        调用方不能借此把原文混入本地 ``attributes``；``span`` 收尾时会单独
        导出这些字段，SQLite trace 始终只保存安全摘要。
        """
        self.export_attributes.update(_export_attributes(values))


_CURRENT_TRACE: ContextVar[TraceContext | None] = ContextVar("wonder_current_trace", default=None)
_CURRENT_SPAN: ContextVar[ActiveSpan | None] = ContextVar("wonder_current_span", default=None)
_OTEL_BRIDGE: _OtelBridge | None | bool = False


def _trace_exporter() -> str:
    """返回启用的 exporter；未知值一律关闭，避免误向外部发送 trace。"""
    exporter = os.environ.get("WONDER_TRACE_EXPORTER", "").strip().casefold()
    return exporter if exporter in {"otlp", "langfuse"} else ""


def _langfuse_exporter_options() -> tuple[str, dict[str, str]] | None:
    """构造 Langfuse OTLP/HTTP 配置，不记录或持久化任何凭据。

    同时兼容项目前缀 ``WONDER_LANGFUSE_*`` 和 Langfuse 常用的
    ``LANGFUSE_*`` 命名。使用 trace 专用 URL，避免不同 OTLP Python
    exporter 对 base endpoint 是否自动补 ``/v1/traces`` 的差异。
    """
    public_key = (
        os.environ.get("WONDER_LANGFUSE_PUBLIC_KEY")
        or os.environ.get("LANGFUSE_PUBLIC_KEY")
        or ""
    ).strip()
    secret_key = (
        os.environ.get("WONDER_LANGFUSE_SECRET_KEY")
        or os.environ.get("LANGFUSE_SECRET_KEY")
        or ""
    ).strip()
    if not public_key or not secret_key:
        return None
    endpoint = (
        os.environ.get("WONDER_LANGFUSE_OTLP_ENDPOINT")
        or os.environ.get("LANGFUSE_OTEL_ENDPOINT")
        or ""
    ).strip()
    if not endpoint:
        base_url = (
            os.environ.get("WONDER_LANGFUSE_BASE_URL")
            or os.environ.get("LANGFUSE_BASE_URL")
            or "https://cloud.langfuse.com"
        ).rstrip("/")
        endpoint = f"{base_url}/api/public/otel/v1/traces"
    credentials = b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    return endpoint, {
        "Authorization": f"Basic {credentials}",
        # Langfuse v4 的直接 OTLP 导入需要此头才能实时出现在 UI 中。
        "x-langfuse-ingestion-version": "4",
    }


def content_summary(value: str) -> dict[str, object]:
    """返回不可逆的内容摘要，供调用方关联重复请求而不保存正文。"""
    encoded = value.encode("utf-8")
    return {"chars": len(value), "sha256": hashlib.sha256(encoded).hexdigest()}


def message_summary(messages: list[dict[str, str]]) -> dict[str, object]:
    """只汇总消息角色、数量和内容哈希，不返回任何消息文本。"""
    normalized = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "input_count": len(messages),
        "input_chars": sum(len(str(item.get("content", ""))) for item in messages),
        "input_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def skill_audit_summary(skills: list[Mapping[str, object]] | None) -> dict[str, object]:
    """将已审计的 Skill 选择压缩为可安全展示的 trace 属性。"""
    items = skills or []
    names = [str(item.get("name", "")) for item in items if item.get("name")]
    versions = [str(item.get("version", "")) for item in items if item.get("version")]
    hashes = [str(item.get("content_hash", "")) for item in items if item.get("content_hash")]
    kinds = [str(item.get("kind", "")) for item in items if item.get("kind")]
    capabilities = [
        str(capability)
        for item in items
        for capability in item.get("capabilities", [])
        if isinstance(capability, str)
    ]
    return {
        "skill_count": len(items),
        "skill_names": names,
        "skill_versions": versions,
        "skill_hashes": hashes,
        "skill_kinds": kinds,
        "skill_capabilities": capabilities,
    }


_CAPTURE_MODES = {"off", "preview", "full"}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)"),
    re.compile(
        r"(?i)((?:deepseek|openai|anthropic|api)[_-]?(?:api[_-]?)?key\s*[:=]\s*)([^\s,;]+)"
    ),
    re.compile(r"(\b(?:sk|dsk)-)[A-Za-z0-9_-]{12,}"),
)


def trace_content_mode() -> str:
    """返回正文采集模式；未知值一律按关闭处理。"""
    mode = os.environ.get("WONDER_TRACE_CAPTURE_CONTENT", "off").strip().casefold()
    return mode if mode in _CAPTURE_MODES else "off"


def _redact_secrets(text: str) -> str:
    value = text
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(r"\1[REDACTED]", value)
    return value


def _captured_text(value: object) -> tuple[str | None, bool]:
    """为 opt-in OTLP 导出准备正文，绝不从这里生成本地持久化属性。"""
    if trace_content_mode() == "off":
        return None, False
    text = _redact_secrets(str(value))
    if trace_content_mode() == "full":
        return text, False
    try:
        limit = max(256, int(os.environ.get("WONDER_TRACE_PREVIEW_CHARS", "12000")))
    except ValueError:
        limit = 12000
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…[trace preview truncated]", True


def _export_attributes(values: Mapping[str, object]) -> dict[str, object]:
    """校验供 OTel 使用的标量，避免 exporter 因复杂 Python 对象失效。"""
    prepared: dict[str, object] = {}
    for raw_key, value in values.items():
        if value is None:
            continue
        key = str(raw_key)
        if isinstance(value, (str, bool, int, float)):
            prepared[key] = value
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, (str, bool, int, float)) for item in value
        ):
            prepared[key] = list(value)
        else:
            prepared[key] = json.dumps(value, ensure_ascii=False, default=str)
    return prepared


def export_llm_input(span: ActiveSpan, messages: list[dict[str, str]]) -> None:
    """将可见 LLM 入参按 OpenInference 字段导出到 Phoenix（显式 opt-in）。"""
    mode = trace_content_mode()
    if mode == "off":
        return
    values: dict[str, object] = {"trace.capture_mode": mode}
    truncated = False
    for index, message in enumerate(messages):
        prefix = f"llm.input_messages.{index}.message"
        values[f"{prefix}.role"] = str(message.get("role", ""))
        content, was_truncated = _captured_text(message.get("content", ""))
        if content is not None:
            values[f"{prefix}.content"] = content
        truncated = truncated or was_truncated
    # 这些字段是 Langfuse 的 OTel 原生映射；保留 OpenInference 字段，以便
    # 同一份 trace 仍可投递 Phoenix 或其他 OpenInference 后端。
    rendered, io_truncated = _captured_text(json.dumps(messages, ensure_ascii=False))
    if rendered is not None:
        values["langfuse.observation.input"] = rendered
    truncated = truncated or io_truncated
    if truncated:
        values["trace.capture_truncated"] = True
    span.set_export_attributes(values)


def export_llm_output(span: ActiveSpan, content: object) -> None:
    """导出 API 的可见 ``message.content``；不读取或导出 reasoning 字段。"""
    captured, truncated = _captured_text(content)
    if captured is None:
        return
    values: dict[str, object] = {
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": captured,
        "langfuse.observation.output": captured,
    }
    if truncated:
        values["trace.capture_truncated"] = True
    span.set_export_attributes(values)


def export_tool_io(span: ActiveSpan, *, arguments: object, result: object) -> None:
    """导出 Agent 工具入参/结果，仅在 ``preview`` 或 ``full`` 调试模式。"""
    arguments_text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    result_text = json.dumps(result, ensure_ascii=False, default=str)
    captured_input, input_truncated = _captured_text(arguments_text)
    captured_output, output_truncated = _captured_text(result_text)
    if captured_input is None:
        return
    values: dict[str, object] = {
        # Phoenix 会在 Tool span 中显示这两个通用 OpenInference I/O 字段。
        "input.value": captured_input,
        "output.value": captured_output or "",
        "langfuse.observation.input": captured_input,
        "langfuse.observation.output": captured_output or "",
    }
    if input_truncated or output_truncated:
        values["trace.capture_truncated"] = True
    span.set_export_attributes(values)


def _safe_attributes(values: Mapping[str, object] | None) -> dict[str, object]:
    if not values:
        return {}
    safe: dict[str, object] = {}
    for raw_key, value in values.items():
        key = str(raw_key)
        lowered = key.casefold()
        if any(part in lowered for part in _SENSITIVE_ATTRIBUTE_PARTS):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            safe[key] = value
        elif isinstance(value, str):
            safe[key] = value[:_MAX_ATTRIBUTE_TEXT]
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, (str, int, float, bool)) for item in value
        ):
            safe[key] = [str(item)[:_MAX_ATTRIBUTE_TEXT] for item in value[:20]]
        else:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            safe[f"{key}_sha256"] = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            safe[f"{key}_chars"] = len(rendered)
    return safe


def _error_summary(error: BaseException) -> str:
    payload = {
        "code": str(getattr(error, "code", type(error).__name__)),
        "type": type(error).__name__,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _otel_bridge() -> _OtelBridge | None:
    """创建一个私有 provider，避免改写宿主进程的全局 provider。"""
    global _OTEL_BRIDGE
    exporter_name = _trace_exporter()
    if not exporter_name:
        return None
    if _OTEL_BRIDGE is not False:
        return _OTEL_BRIDGE or None
    try:
        from opentelemetry import context as otel_context
        from opentelemetry import trace as otel_trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        headers: dict[str, str] | None = None
        if exporter_name == "langfuse":
            langfuse_options = _langfuse_exporter_options()
            if langfuse_options is None:
                return None
            endpoint, headers = langfuse_options
        else:
            endpoint = os.environ.get("WONDER_OTLP_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
        project_name = os.environ.get("WONDER_TRACE_PROJECT", "wonder-stockresearch")
        provider = TracerProvider(
            resource=Resource.create(
                {
                    SERVICE_NAME: project_name,
                    # Phoenix/OpenInference 用此资源属性决定项目；service.name 仍保留给
                    # 通用 OTel collector 与其他后端。
                    "openinference.project.name": project_name,
                }
            )
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers)))
        _OTEL_BRIDGE = _OtelBridge(
            tracer=provider.get_tracer("wonder_stockresearch"),
            provider=provider,
            trace_api=otel_trace,
            context_api=otel_context,
        )
    except (ImportError, TypeError, ValueError):
        # 可选依赖缺失或配置格式不兼容时，继续保留本地 trace；不要中断研究。
        _OTEL_BRIDGE = None
    return _OTEL_BRIDGE or None


def _otel_start(
    bridge: _OtelBridge | None,
    name: str,
    *,
    parent: ActiveSpan | TraceContext | None,
    span_kind: str,
    attributes: Mapping[str, object],
) -> tuple[Any | None, Any | None]:
    if bridge is None:
        return None, None
    try:
        parent_span = parent.otel_span if parent is not None else None
        parent_context = (
            bridge.trace_api.set_span_in_context(parent_span) if parent_span is not None else None
        )
        otel_attributes = dict(_safe_attributes(attributes))
        otel_attributes["openinference.span.kind"] = span_kind.upper()
        langfuse_types = {
            "agent": "agent",
            "chain": "chain",
            "llm": "generation",
            "tool": "tool",
        }
        otel_attributes["langfuse.observation.type"] = langfuse_types.get(span_kind, "span")
        trace_context = _CURRENT_TRACE.get()
        if trace_context is not None:
            otel_attributes["langfuse.trace.name"] = trace_context.trace_kind
            if trace_context.session_id:
                # 让同一次开放问题会话的 ask / follow-up traces 在 Langfuse Sessions
                # 中归组；不会记录用户身份，也不影响本地 SQLite trace。
                otel_attributes["langfuse.session.id"] = trace_context.session_id
        span = bridge.tracer.start_span(name, context=parent_context, attributes=otel_attributes)
        token = bridge.context_api.attach(bridge.trace_api.set_span_in_context(span))
        return span, token
    except (AttributeError, RuntimeError, TypeError, ValueError):
        # 可观测性不能成为正常业务的失败原因。
        return None, None


def _otel_finish(
    bridge: _OtelBridge | None,
    span: Any | None,
    token: Any | None,
    *,
    attributes: Mapping[str, object],
    export_attributes: Mapping[str, object] | None = None,
    error: BaseException | None,
) -> None:
    if bridge is None or span is None:
        return
    try:
        for key, value in _safe_attributes(attributes).items():
            span.set_attribute(key, value)
        # ``export_attributes`` 只来自 export_llm_* / export_tool_io，且不会写入
        # SQLite。这样开启本机调试时，Phoenix 可以展示正文，而 trace-read 仍安全。
        for key, value in _export_attributes(export_attributes or {}).items():
            span.set_attribute(key, value)
        if error is not None:
            # 不调用 record_exception：部分异常文本可能含服务端返回片段或用户输入。
            span.set_attribute("error.type", type(error).__name__)
            span.set_attribute("error.code", str(getattr(error, "code", type(error).__name__))[:128])
            span.set_status(bridge.trace_api.Status(bridge.trace_api.StatusCode.ERROR))
        span.end()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return
    finally:
        if token is not None:
            try:
                bridge.context_api.detach(token)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return


def _force_flush(bridge: _OtelBridge) -> None:
    try:
        bridge.provider.force_flush(timeout_millis=1_000)
    except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
        # 远端 collector 的瞬时故障不得改变本地研究或 Agent 状态。
        return


def link_current_trace(root: Path, *, turn_id: str, attempt_id: str) -> None:
    """把已打开的顶层 trace 关联到其后创建的 Agent Attempt。"""
    context = _CURRENT_TRACE.get()
    if context is None:
        return
    context.turn_id = turn_id
    context.attempt_id = attempt_id
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.update_trace_association(
            connection, context.trace_id, turn_id=turn_id, attempt_id=attempt_id
        )


@contextmanager
def operation(
    root: Path,
    name: str,
    *,
    trace_kind: str,
    run_id: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    attempt_id: str | None = None,
    attributes: Mapping[str, object] | None = None,
) -> Iterator[TraceContext | ActiveSpan]:
    """创建顶层 trace；嵌套时降为同一个 trace 中的 operation span。"""
    parent = _CURRENT_TRACE.get()
    if parent is not None:
        with span(root, name, span_kind="chain", attributes=attributes) as child:
            yield child
        return

    root = Path(root)
    started_at = storage.now_utc()
    trace_id = str(uuid4())
    safe_attributes = _safe_attributes(attributes)
    storage.initialize(root)
    with storage.project_lock(root), storage.open_db(root) as connection:
        storage.insert_trace(
            connection,
            trace_id=trace_id,
            trace_kind=trace_kind,
            name=name,
            run_id=run_id,
            session_id=session_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            attributes_json=json.dumps(safe_attributes, ensure_ascii=False, sort_keys=True),
            started_at=started_at,
        )
    bridge = _otel_bridge()
    context = TraceContext(
        root=root,
        trace_id=trace_id,
        trace_kind=trace_kind,
        run_id=run_id,
        session_id=session_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
        attributes=safe_attributes,
    )
    root_span_kind = "agent" if trace_kind == "agent" else "chain"
    root_otel_attributes = dict(safe_attributes)
    root_otel_attributes["langfuse.trace.name"] = name
    if session_id:
        root_otel_attributes["langfuse.session.id"] = session_id
    context.otel_span, context.otel_token = _otel_start(
        bridge, name, parent=None, span_kind=root_span_kind, attributes=root_otel_attributes
    )
    trace_token: Token[TraceContext | None] = _CURRENT_TRACE.set(context)
    try:
        yield context
    except BaseException as error:
        finished_at = storage.now_utc()
        with storage.project_lock(root), storage.open_db(root) as connection:
            storage.finish_trace(
                connection,
                trace_id,
                attributes_json=json.dumps(context.attributes, ensure_ascii=False, sort_keys=True),
                status="failed",
                error_json=_error_summary(error),
                finished_at=finished_at,
            )
        _otel_finish(
            bridge,
            context.otel_span,
            context.otel_token,
            attributes=context.attributes,
            export_attributes=None,
            error=error,
        )
        raise
    else:
        finished_at = storage.now_utc()
        with storage.project_lock(root), storage.open_db(root) as connection:
            storage.finish_trace(
                connection,
                trace_id,
                attributes_json=json.dumps(context.attributes, ensure_ascii=False, sort_keys=True),
                status="succeeded",
                error_json=None,
                finished_at=finished_at,
            )
        _otel_finish(
            bridge,
            context.otel_span,
            context.otel_token,
            attributes=context.attributes,
            export_attributes=None,
            error=None,
        )
    finally:
        _CURRENT_TRACE.reset(trace_token)
        if bridge is not None:
            _force_flush(bridge)


@contextmanager
def span(
    root: Path,
    name: str,
    *,
    span_kind: str,
    attributes: Mapping[str, object] | None = None,
) -> Iterator[ActiveSpan]:
    """在活动 trace 内记录一个 span；没有活动 trace 时为安全 no-op。"""
    context = _CURRENT_TRACE.get()
    if context is None:
        yield ActiveSpan(root=Path(root), trace_id="", span_id="")
        return
    parent = _CURRENT_SPAN.get()
    started_at = storage.now_utc()
    active = ActiveSpan(
        root=Path(root),
        trace_id=context.trace_id,
        span_id=str(uuid4()),
        attributes=_safe_attributes(attributes),
    )
    with storage.project_lock(context.root), storage.open_db(context.root) as connection:
        storage.insert_trace_span(
            connection,
            span_id=active.span_id,
            trace_id=context.trace_id,
            parent_span_id=parent.span_id if parent else None,
            span_kind=span_kind,
            name=name,
            attributes_json=json.dumps(active.attributes, ensure_ascii=False, sort_keys=True),
            started_at=started_at,
        )
    bridge = _otel_bridge()
    active.otel_span, active.otel_token = _otel_start(
        bridge, name, parent=parent or context, span_kind=span_kind, attributes=active.attributes
    )
    span_token: Token[ActiveSpan | None] = _CURRENT_SPAN.set(active)
    try:
        yield active
    except BaseException as error:
        with storage.project_lock(context.root), storage.open_db(context.root) as connection:
            storage.finish_trace_span(
                connection,
                active.span_id,
                attributes_json=json.dumps(active.attributes, ensure_ascii=False, sort_keys=True),
                status="failed",
                error_json=_error_summary(error),
                finished_at=storage.now_utc(),
            )
        _otel_finish(
            bridge,
            active.otel_span,
            active.otel_token,
            attributes=active.attributes,
            export_attributes=active.export_attributes,
            error=error,
        )
        raise
    else:
        active.set_attribute("duration_ms", round((perf_counter() - active._started) * 1000, 3))
        with storage.project_lock(context.root), storage.open_db(context.root) as connection:
            storage.finish_trace_span(
                connection,
                active.span_id,
                attributes_json=json.dumps(active.attributes, ensure_ascii=False, sort_keys=True),
                status="succeeded",
                error_json=None,
                finished_at=storage.now_utc(),
            )
        _otel_finish(
            bridge,
            active.otel_span,
            active.otel_token,
            attributes=active.attributes,
            export_attributes=active.export_attributes,
            error=None,
        )
    finally:
        _CURRENT_SPAN.reset(span_token)


def _decode_trace_row(row: dict[str, Any]) -> dict[str, object]:
    payload: dict[str, object] = {key: value for key, value in row.items() if key not in {"attributes_json", "error_json"}}
    payload["attributes"] = json.loads(str(row["attributes_json"]))
    payload["error"] = json.loads(str(row["error_json"])) if row.get("error_json") else None
    return payload


def _read_traces(root: Path, trace_rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    with storage.open_db(root) as connection:
        result: list[dict[str, object]] = []
        for row in trace_rows:
            trace = _decode_trace_row(row)
            trace["spans"] = [_decode_trace_row(item) for item in storage.list_trace_spans(connection, str(row["id"]))]
            result.append(trace)
    return result


def read_run_trace(root: Path, run_id: str) -> dict[str, object]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        traces = storage.list_traces_for_run(connection, run_id)
    return {"run_id": run_id, "traces": _read_traces(root, traces)}


def read_agent_trace(root: Path, session_id: str) -> dict[str, object]:
    storage.initialize(root)
    with storage.open_db(root) as connection:
        traces = storage.list_traces_for_session(connection, session_id)
    return {"session_id": session_id, "traces": _read_traces(root, traces)}
