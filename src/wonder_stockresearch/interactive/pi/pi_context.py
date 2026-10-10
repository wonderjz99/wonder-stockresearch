"""Pi 原生消息的可见投影、配对恢复和有界上下文。"""

from __future__ import annotations

import copy
import json
from typing import Any

from wonder_stockresearch.interactive import agent_memory, agent_results


def visible(message: dict[str, Any]) -> dict[str, Any]:
    role = message.get("role")
    if role not in {"user", "assistant", "toolResult"}:
        raise ValueError("invalid_message_role")
    result = {"role": role, "timestamp": message.get("timestamp", 0)}
    content = message.get("content", [])
    if isinstance(content, str) and role == "user":
        result["content"] = content
    else:
        result["content"] = []
        for item in content:
            if item.get("type") == "text":
                result["content"].append({"type": "text", "text": str(item["text"])})
            elif role == "assistant" and item.get("type") == "toolCall":
                result["content"].append(
                    {key: item[key] for key in ("type", "id", "name", "arguments")}
                )
    if role == "assistant":
        result.update(
            {
                key: message[key]
                for key in ("api", "provider", "model", "stopReason", "usage")
                if key in message
            }
        )
    if role == "toolResult":
        result.update({key: message[key] for key in ("toolCallId", "toolName", "isError")})
    return result


def groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """只恢复完整调用组；崩溃遗留的半组仍在数据库审计中，不发给模型。"""
    result: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        calls = (
            [
                item
                for item in message.get("content", [])
                if isinstance(item, dict) and item.get("type") == "toolCall"
            ]
            if message["role"] == "assistant"
            else []
        )
        if calls:
            expected = {item["id"] for item in calls}
            batch = messages[index + 1 : index + 1 + len(calls)]
            if (
                len(batch) == len(calls)
                and all(item["role"] == "toolResult" for item in batch)
                and {item["toolCallId"] for item in batch} == expected
            ):
                result.append([message, *batch])
                index += len(calls)
        elif message["role"] != "toolResult":
            result.append([message])
        index += 1
    return result


def text(message: dict[str, Any]) -> str:
    content = message.get("content", [])
    if isinstance(content, str):
        return content
    return "\n".join(item["text"] for item in content if item.get("type") == "text")


def tool_projection(result: dict[str, Any], *, token_limit: int = 1500) -> str:
    """兼容入口；领域投影由两种运行时共用。"""
    return agent_results.model_projection(result, token_limit=token_limit)


def _short(value: Any, depth: int = 0) -> Any:
    """有界且有效的 JSON 工作摘要；数字不截断，字符串明确标记摘录。"""
    if isinstance(value, str):
        return value if len(value) <= 240 else value[:240] + "…（摘录）"
    if depth >= 5 and isinstance(value, (dict, list)):
        return {"summary_only": True, "count": len(value)}
    if isinstance(value, dict):
        result = {key: _short(item, depth + 1) for key, item in list(value.items())[:12]}
        if len(value) > 12:
            result["omitted_keys"] = len(value) - 12
        return result
    if isinstance(value, list):
        return [_short(item, depth + 1) for item in value[:6]]
    return value


def working_memory(messages: list[dict[str, Any]]) -> str:
    """从当前 Turn 的可见工具记录重建，不引入额外模型或跨 Turn 证据授权。"""
    calls = {}
    entries = []
    for message in messages:
        if message["role"] == "assistant":
            for item in message.get("content", []):
                if item.get("type") == "toolCall":
                    calls[item["id"]] = item["arguments"]
        if message["role"] != "toolResult":
            continue
        try:
            payload = json.loads(text(message))
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        selected = {
            key: payload[key]
            for key in (
                "analysis_id",
                "status",
                "row_count",
                "total_matches",
                "returned_records",
                "offset",
                "next_offset",
                "has_more",
                "context_truncated",
                "next_step",
                "result",
                "input_summary",
                "error",
            )
            if key in payload
        }
        for key in ("records", "rows"):
            if isinstance(payload.get(key), list):
                selected["sample"] = [
                    {
                        field: value
                        for field, value in item.items()
                        if field
                        in {
                            "fact_id",
                            "field_key",
                            "label",
                            "period",
                            "value_decimal",
                            "value",
                            "value_json",
                            "unit",
                            "scale",
                            "currency",
                            "source_id",
                            "locator",
                            "ref_id",
                        }
                    }
                    for item in payload[key][:3]
                    if isinstance(item, dict)
                ]
        entries.append(
            {
                "tool": message["toolName"],
                "arguments": calls.get(message["toolCallId"], {}),
                "is_error": message.get("isError", False),
                "observed": selected,
            }
        )
    if not entries:
        return ""
    view = {
        "recent_operations": [_short(item) for item in entries[-6:]],
        "note": "工作记录仅帮助定位，不证明结论。sample 是部分记录；不要重复成功查询，继续分页或用 analysis_id 计算。",
    }
    while len(view["recent_operations"]) > 1:
        encoded = json.dumps(view, ensure_ascii=False, separators=(",", ":"))
        if agent_memory.estimate_input_tokens([{"content": encoded}]) <= 1400:
            return encoded
        view["recent_operations"].pop(0)
    last = view["recent_operations"][0]
    last["observed"] = json.loads(tool_projection(last["observed"], token_limit=600))
    return json.dumps(view, ensure_ascii=False, separators=(",", ":"))


def recovery_hint(code: str) -> str:
    """兼容入口；不在 Pi 消息适配层定义领域错误规则。"""
    return agent_results.recovery_hint(code)


def estimate(system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> int:
    return agent_memory.estimate_input_tokens(
        [
            {"content": system},
            {"content": json.dumps(tools, ensure_ascii=False)},
            *[{"content": json.dumps(message, ensure_ascii=False)} for message in messages],
        ]
    )


def project(
    messages: list[dict[str, Any]], system: str, tools: list[dict[str, Any]], target: int
) -> list[dict[str, Any]]:
    batches = groups([visible(item) for item in messages])
    # 保留原问题、最新用户补充和最新完整工具组；按组淘汰，不能制造孤立 toolResult。
    protected = {0, len(batches) - 1}
    user_indices = [i for i, batch in enumerate(batches) if batch[0]["role"] == "user"]
    if user_indices:
        protected.add(user_indices[-1])
    selected = set(range(len(batches)))
    for index in range(len(batches)):
        flattened = [item for i, batch in enumerate(batches) if i in selected for item in batch]
        if estimate(system, tools, flattened) <= target:
            return flattened
        if index not in protected:
            selected.discard(index)
    flattened = copy.deepcopy(
        [item for i, batch in enumerate(batches) if i in selected for item in batch]
    )
    for limit in (900, 400, 200):
        for message in flattened:
            if message["role"] == "toolResult":
                try:
                    payload = json.loads(text(message))
                    message["content"] = [
                        {"type": "text", "text": tool_projection(payload, token_limit=limit)}
                    ]
                except (ValueError, TypeError):
                    pass
        if estimate(system, tools, flattened) <= target:
            break
    return flattened


def scripted_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """兼容既有测试注入器；生产模型只接收 Pi 原生消息。"""
    result = [{"role": "system", "content": system}]
    users = [text(item) for item in messages if item["role"] == "user"]
    for message in messages:
        if message["role"] == "user" and text(message).startswith(
            ("运行状态：", "本 Turn 工作记录：")
        ):
            continue
        if message["role"] == "toolResult":
            label = "返回可恢复错误" if message.get("isError") else "返回"
            result.append(
                {
                    "role": "user",
                    "content": f"工具 {message['toolName']} {label}：{text(message)}\n请继续处理原问题。",
                }
            )
        else:
            result.append({"role": str(message["role"]), "content": text(message)})
    if len(result) > 1 and result[1]["role"] == "user":
        result[1]["content"] = "\n".join(users)
    return result
