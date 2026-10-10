"""Writer、Router 和兼容问答共用的单次 Python 模型请求。

这里只处理 DeepSeek 配置、HTTP、可见响应和调用 trace；调用次数、预算预留、
输出协议与业务保存分别由调用方负责。固定研究与分类无需启动 Node/Pi。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from . import observability

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_THINKING = "disabled"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 16000
THINKING_MODES = ("enabled", "disabled")
REQUEST_CONNECT_TIMEOUT_SECONDS = 10
REQUEST_READ_TIMEOUT_SECONDS = 300


class ModelRequestError(Exception):
    """不包含凭据、供应商错误正文或隐藏推理的模型边界错误。"""

    def __init__(self, code: str, message: str, *, exit_code: int = 1) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


def api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ModelRequestError(
            "missing_api_key",
            "未设置环境变量 DEEPSEEK_API_KEY；请在你的运行环境配置后重试",
            exit_code=2,
        )
    return key


def base_url() -> str:
    return os.environ.get("WONDER_DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def resolved_model(model: str | None) -> str:
    return model or os.environ.get("WONDER_DEEPSEEK_MODEL", DEFAULT_MODEL)


def max_tokens() -> int:
    raw = os.environ.get("WONDER_DEEPSEEK_MAX_TOKENS")
    if raw and raw.isdigit():
        return max(256, int(raw))
    return DEFAULT_MAX_TOKENS


def resolve_thinking(value: str | None) -> str:
    mode = value or os.environ.get("WONDER_DEEPSEEK_THINKING", DEFAULT_THINKING)
    if mode not in THINKING_MODES:
        raise ModelRequestError(
            "invalid_thinking", f"thinking 只能是 {'/'.join(THINKING_MODES)}", exit_code=2
        )
    return mode


def resolve_temperature(value: float | None) -> float:
    if value is None:
        raw = os.environ.get("WONDER_DEEPSEEK_TEMPERATURE")
        if raw:
            try:
                value = float(raw)
            except ValueError as error:
                raise ModelRequestError(
                    "invalid_temperature", "WONDER_DEEPSEEK_TEMPERATURE 不是数字", exit_code=2
                ) from error
        else:
            value = DEFAULT_TEMPERATURE
    if not 0.0 <= value <= 2.0:
        raise ModelRequestError("invalid_temperature", "temperature 必须在 0–2 之间", exit_code=2)
    return value


def request_deepseek(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
    thinking: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """执行一次 JSON 请求；没有自动重试，异常文本不采用供应商返回正文。"""
    # 函数参数与公共配置函数同名，默认上限在独立 helper 中解析。
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": _request_max_tokens(max_tokens),
        "stream": False,
        "temperature": resolve_temperature(temperature),
        "thinking": {"type": resolve_thinking(thinking)},
    }
    try:
        response = requests.post(
            f"{base_url()}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=(REQUEST_CONNECT_TIMEOUT_SECONDS, REQUEST_READ_TIMEOUT_SECONDS),
        )
    except requests.RequestException as error:
        raise ModelRequestError(
            "request_failed", f"DeepSeek 请求失败：{type(error).__name__}"
        ) from error
    if response.status_code in {401, 403}:
        raise ModelRequestError("unauthorized", "DeepSeek 拒绝了凭据（请检查 DEEPSEEK_API_KEY）")
    if response.status_code >= 400:
        # error.message 可能回显认证头或完整请求，不能进入业务错误和本地 trace。
        raise ModelRequestError("http_error", f"DeepSeek 返回 HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as error:
        raise ModelRequestError("malformed_response", "DeepSeek 响应不是合法 JSON") from error
    if not isinstance(data, dict):
        raise ModelRequestError("malformed_response", "DeepSeek 响应结构异常")
    return data


def _request_max_tokens(value: int | None) -> int:
    return max_tokens() if value is None else value


def extract_content(response: dict[str, Any]) -> str:
    """只读取可见 content，不以 reasoning_content 作为缺失回答的替代。"""
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ModelRequestError(
            "malformed_response", "DeepSeek 响应缺少 choices[0].message.content"
        ) from error
    if not isinstance(content, str) or not content.strip():
        raise ModelRequestError("empty_content", "DeepSeek 返回空内容")
    return content


def usage_summary(response: dict[str, Any]) -> dict[str, int] | None:
    """只保留供应商明确返回的非负整数；bool 不能作为 token 用量。"""
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    summary = {
        key: usage[key] for key in keys
        if type(usage.get(key)) is int and usage[key] >= 0
    }
    return summary or None


def finish_reason(response: dict[str, Any]) -> str | None:
    try:
        reason = response["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return None
    return reason if isinstance(reason, str) else None


def visible_response_content(response: dict[str, Any]) -> str | None:
    """取得 API 可见回答供 opt-in trace 使用，不读取隐藏推理。"""
    try:
        content = response["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return content if isinstance(content, str) else None


def traced_request(
    root: Path,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    thinking: str | None,
    kind: str,
    max_tokens: int | None = None,
    request: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """记录一次请求的安全摘要；request 保留 Writer 的历史离线注入边界。"""
    attributes: dict[str, object] = {
        "provider": "deepseek",
        "model": model,
        "llm.system": "deepseek",
        "llm.provider": "deepseek",
        "llm.model_name": model,
        "llm.request.model_name": model,
        "call_kind": kind,
        "temperature": resolve_temperature(temperature),
        "thinking": resolve_thinking(thinking),
        **observability.message_summary(messages),
    }
    with observability.span(
        Path(root), "deepseek.chat.completions", span_kind="llm", attributes=attributes
    ) as trace_span:
        observability.export_llm_input(trace_span, messages)
        request_limits = {} if max_tokens is None else {"max_tokens": max_tokens}
        response = (request or request_deepseek)(
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=temperature,
            thinking=thinking,
            **request_limits,
        )
        usage = usage_summary(response) or {}
        trace_span.set_attributes(
            {
                "input_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "llm.token_count.prompt": usage.get("prompt_tokens"),
                "llm.token_count.completion": usage.get("completion_tokens"),
                "llm.token_count.total": usage.get("total_tokens"),
                "finish_reason": finish_reason(response),
            }
        )
        visible_content = visible_response_content(response)
        if visible_content is not None:
            observability.export_llm_output(trace_span, visible_content)
        return response
