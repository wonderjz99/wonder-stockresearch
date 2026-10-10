"""交互 Agent 的最小模型调用边界。

生产默认仍使用 DeepSeek；这个薄适配层让离线测试和演示可以注入脚本化决策，
不引入多模型路由、Agent host 或框架运行时。
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from wonder_stockresearch import envfile, model_request
from wonder_stockresearch.interactive import agent_memory
from wonder_stockresearch.interactive.agent_errors import AgentError

AGENT_INPUT_TARGET_TOKENS = 5000
MAX_AGENT_INPUT_TOKENS = 8000
MAX_AGENT_OUTPUT_TOKENS = 4096

# 在同步调用边界传递本 Turn 的剩余预算，保持现有三参数模型适配接口。
remaining_request_tokens: ContextVar[int | None] = ContextVar(
    "remaining_request_tokens", default=None
)


def deepseek_decision(
    root: Path, messages: list[dict[str, str]], model: str
) -> tuple[dict[str, Any], int]:
    """兼容问答的一次决定；即使 JSON 无效也保留已经发生的 token 消耗。"""
    consumed: int | None = None
    try:
        envfile.load_env_file(root / ".env")
        remaining = remaining_request_tokens.get()
        limits: dict[str, int] = {}
        if remaining is not None:
            estimated_input = agent_memory.estimate_input_tokens(messages)
            if estimated_input >= remaining:
                raise AgentError(
                    "agent_budget_exhausted",
                    f"剩余预算 {remaining} tokens，输入估算 {estimated_input} tokens；"
                    "压缩后仍无法容纳完整规则与问题，请缩小问题或为新 Turn 显式设置预算",
                    consumed_tokens=0,
                )
            limits["max_tokens"] = min(
                MAX_AGENT_OUTPUT_TOKENS, model_request.max_tokens(), remaining - estimated_input
            )
        response = model_request.traced_request(
            root,
            api_key=model_request.api_key(),
            model=model,
            messages=messages,
            temperature=model_request.DEFAULT_TEMPERATURE,
            thinking=model_request.DEFAULT_THINKING,
            kind="agent_decision",
            **limits,
        )
        usage = model_request.usage_summary(response) or {}
        if "total_tokens" not in usage:
            raise AgentError("model_usage_unavailable", "模型未返回用量，保留预留预算并停止")
        consumed = usage["total_tokens"]
        return json.loads(model_request.extract_content(response)), consumed
    except model_request.ModelRequestError as error:
        raise AgentError(
            error.code, error.message, exit_code=error.exit_code, consumed_tokens=consumed
        ) from error
    except json.JSONDecodeError as error:
        raise AgentError(
            "invalid_decision", "Agent 输出不是合法 JSON", consumed_tokens=consumed
        ) from error


class AgentModelClient(Protocol):
    def decide(
        self, root: Path, messages: list[dict[str, str]], model: str
    ) -> tuple[dict[str, Any], int]: ...


@dataclass
class FunctionAgentModelClient:
    """把既有的 DeepSeek 调用函数包装为可替换 client。"""

    call: Callable[[Path, list[dict[str, str]], str], tuple[dict[str, Any], int]]

    def decide(
        self, root: Path, messages: list[dict[str, str]], model: str
    ) -> tuple[dict[str, Any], int]:
        return self.call(root, messages, model)


@dataclass
class ScriptedAgentModelClient:
    """只供离线测试/示例使用的确定性模型 client。"""

    responses: Sequence[tuple[dict[str, Any], int]]
    _pending: deque[tuple[dict[str, Any], int]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._pending = deque(self.responses)

    def decide(
        self, _root: Path, _messages: list[dict[str, str]], _model: str
    ) -> tuple[dict[str, Any], int]:
        if not self._pending:
            raise RuntimeError("scripted_agent_responses_exhausted")
        return self._pending.popleft()
