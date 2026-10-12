"""Python/Pi 的版本化 JSONL 契约；只校验可见消息与执行边界。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.models import StrictModel

PROTOCOL_VERSION = 1
MAX_SAFE_INTEGER = 2**53 - 1
Count = Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
PositiveCount = Annotated[StrictInt, Field(gt=0, le=MAX_SAFE_INTEGER)]
SafeNumber = Annotated[StrictInt | StrictFloat, Field(ge=0, allow_inf_nan=False)]
Identifier = Annotated[StrictStr, Field(min_length=1)]


class ProtocolModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class VersionedModel(ProtocolModel):
    protocol_version: StrictInt

    @field_validator("protocol_version")
    @classmethod
    def supported_version(cls, value: int) -> int:
        if value != PROTOCOL_VERSION:
            raise ValueError("unsupported_protocol_version")
        return value


class TextBlock(ProtocolModel):
    type: Literal["text"]
    text: StrictStr


class ToolCallBlock(ProtocolModel):
    type: Literal["toolCall"]
    id: Identifier
    name: Identifier
    arguments: dict[str, JsonValue]


class UsageCost(ProtocolModel):
    input: SafeNumber = 0
    output: SafeNumber = 0
    cacheRead: SafeNumber = 0
    cacheWrite: SafeNumber = 0
    total: SafeNumber = 0


class Usage(ProtocolModel):
    input: Count = 0
    output: Count = 0
    cacheRead: Count = 0
    cacheWrite: Count = 0
    totalTokens: Count = 0
    cost: UsageCost = Field(default_factory=UsageCost)


class UserMessage(ProtocolModel):
    role: Literal["user"]
    content: StrictStr | list[TextBlock]
    timestamp: SafeNumber = 0


class AssistantMessage(ProtocolModel):
    role: Literal["assistant"]
    content: list[Annotated[TextBlock | ToolCallBlock, Field(discriminator="type")]]
    timestamp: SafeNumber = 0
    api: StrictStr = ""
    provider: StrictStr = ""
    model: StrictStr = ""
    stopReason: Literal["stop", "length", "toolUse", "error", "aborted"] = "stop"
    usage: Usage = Field(default_factory=Usage)


class ToolResultMessage(ProtocolModel):
    role: Literal["toolResult"]
    content: list[TextBlock]
    toolCallId: Identifier
    toolName: Identifier
    isError: StrictBool
    timestamp: SafeNumber = 0


VisibleMessage = Annotated[
    UserMessage | AssistantMessage | ToolResultMessage, Field(discriminator="role")
]
VISIBLE_MESSAGES = TypeAdapter(list[VisibleMessage])


class ToolConfig(ProtocolModel):
    name: Identifier
    description: StrictStr
    arguments: dict[str, JsonValue]


class InitConfig(VersionedModel):
    model: Identifier
    system: StrictStr
    tools: list[ToolConfig]
    prompt: StrictStr
    history: list[VisibleMessage] = Field(default_factory=list)
    scripted: StrictBool = False

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, tools: list[ToolConfig]) -> list[ToolConfig]:
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("duplicate_tool_name")
        return tools


class EmptyPayload(ProtocolModel):
    pass


class MessagesPayload(ProtocolModel):
    messages: list[VisibleMessage]


class ModelEndPayload(ProtocolModel):
    message: AssistantMessage
    usageKnown: StrictBool
    consumed: Count | None

    @model_validator(mode="after")
    def consistent_usage(self) -> ModelEndPayload:
        if self.usageKnown != (self.consumed is not None):
            raise ValueError("inconsistent_usage_receipt")
        return self


class ToolPayload(ProtocolModel):
    toolCallId: Identifier
    name: Identifier
    arguments: dict[str, JsonValue]


class MessagePayload(ProtocolModel):
    message: ToolResultMessage


class LifecyclePayload(ProtocolModel):
    type: Literal[
        "agent_start",
        "agent_end",
        "turn_start",
        "turn_end",
        "tool_execution_start",
        "tool_execution_end",
    ]
    toolCallId: Identifier | None = None
    toolName: Identifier | None = None
    isError: StrictBool | None = None


Event = Literal[
    "ready",
    "context",
    "model_start",
    "scripted_model",
    "model_end",
    "tool_check",
    "tool",
    "message",
    "lifecycle",
    "settled",
]
PAYLOAD_MODELS: dict[str, type[ProtocolModel]] = {
    "ready": VersionedModel,
    "context": MessagesPayload,
    "model_start": MessagesPayload,
    "scripted_model": MessagesPayload,
    "model_end": ModelEndPayload,
    "tool_check": ToolPayload,
    "tool": ToolPayload,
    "message": MessagePayload,
    "lifecycle": LifecyclePayload,
    "settled": EmptyPayload,
}


class Request(VersionedModel):
    id: PositiveCount
    event: Event
    payload: dict[str, JsonValue]

    @field_validator("payload")
    @classmethod
    def event_payload(cls, value: dict[str, JsonValue], info: Any) -> dict[str, Any]:
        event = info.data.get("event")
        if event not in PAYLOAD_MODELS:
            raise ValueError("unknown_event")
        parsed = PAYLOAD_MODELS[event].model_validate(value)
        return parsed.model_dump(mode="json", exclude_unset=True)


class DoneFrame(VersionedModel):
    event: Literal["done"]


class FailedFrame(VersionedModel):
    event: Literal["failed"]
    code: Literal["pi_runtime_failed"]


FRAME = TypeAdapter(Annotated[Request | DoneFrame | FailedFrame, Field(discriminator="event")])


class ModelLimits(ProtocolModel):
    max_tokens: PositiveCount
    repair_only: StrictBool = False


class ModelResult(ProtocolModel):
    repair: StrictStr | None = None


class ToolError(ProtocolModel):
    code: Identifier
    message: StrictStr
    next_step_hint: StrictStr | None = None
    is_error: StrictBool = True
    recoverable: StrictBool = False
    executed: StrictBool | None = None
    analysis_id: StrictStr | None = None
    node: StrictStr | None = None
    line: Count | None = None
    column: Count | None = None


class ToolResult(ProtocolModel):
    content: StrictStr | None = None
    details: dict[str, JsonValue] | None = None
    error: ToolError | None = None

    @model_validator(mode="after")
    def content_or_error(self) -> ToolResult:
        if self.content is None and self.error is None:
            raise ValueError("missing_tool_result")
        return self


class ScriptedResult(ProtocolModel):
    decision: dict[str, JsonValue]
    consumed: Count


RESULT_MODELS: dict[str, type[ProtocolModel]] = {
    "ready": VersionedModel,
    "model_start": ModelLimits,
    "model_end": ModelResult,
    "tool": ToolResult,
    "scripted_model": ScriptedResult,
    "tool_check": EmptyPayload,
    "message": EmptyPayload,
    "lifecycle": EmptyPayload,
    "settled": EmptyPayload,
}


def _validate(adapter: Any, value: object, label: str) -> Any:
    try:
        # Pydantic's JsonValue permits floats; wire JSON must remain finite at every depth.
        json.dumps(value, allow_nan=False)
        if isinstance(adapter, TypeAdapter):
            return adapter.validate_python(value)
        return adapter.model_validate(value)
    except (ValidationError, TypeError, ValueError, OverflowError) as error:
        raise AgentError("pi_protocol_error", f"Pi {label}不符合协议") from error


def validate_config(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentError("pi_protocol_error", "Pi 初始化配置不符合协议")
    config = {"protocol_version": PROTOCOL_VERSION, **value}
    return _validate(InitConfig, config, "初始化配置").model_dump(mode="json", exclude_unset=True)


def validate_result(event: str, value: object) -> Any:
    if event == "context":
        messages = _validate(VISIBLE_MESSAGES, value, "上下文响应")
        return [message.model_dump(mode="json", exclude_unset=True) for message in messages]
    model = RESULT_MODELS.get(event)
    if model is None:
        raise AgentError("pi_protocol_error", "Pi 响应事件不符合协议")
    return _validate(model, value, "响应").model_dump(mode="json", exclude_unset=True)


def reply(request: Request, result: object) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "id": request.id,
        "ok": True,
        "result": validate_result(request.event, result),
    }


@dataclass
class ProtocolSession:
    ready: bool = False
    last_id: int = 0
    terminal: bool = False

    def accept(self, value: object) -> Request | DoneFrame | FailedFrame:
        frame = _validate(FRAME, value, "请求")
        if self.terminal:
            raise AgentError("pi_protocol_error", "Pi 已结束，不能继续发送消息")
        if isinstance(frame, FailedFrame):
            self.terminal = True
            return frame
        if isinstance(frame, DoneFrame):
            if not self.ready:
                raise AgentError("pi_protocol_error", "Pi 尚未完成版本握手")
            self.terminal = True
            return frame
        if frame.id <= self.last_id:
            raise AgentError("pi_protocol_error", "Pi 请求 ID 必须严格递增")
        if not self.ready and frame.event != "ready":
            raise AgentError("pi_protocol_error", "Pi 尚未完成版本握手")
        if self.ready and frame.event == "ready":
            raise AgentError("pi_protocol_error", "Pi 不能重复版本握手")
        self.last_id = frame.id
        self.ready = True
        return frame
