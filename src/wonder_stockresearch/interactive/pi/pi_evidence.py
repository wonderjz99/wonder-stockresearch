"""Pi 提示中的紧凑引用；只从当前 Turn 的权威回执生成，展开后仍须原校验。"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from wonder_stockresearch import storage
from wonder_stockresearch.interactive import agent_memory
from wonder_stockresearch.interactive.agent_errors import AgentError


def reference_id(source_id: str, locator: str) -> str:
    digest = hashlib.sha256(
        json.dumps([source_id, locator], ensure_ascii=False).encode()
    ).hexdigest()
    return "ref_" + digest[:20]


def catalog(root: Path, turn_id: str) -> dict[str, dict[str, str]]:
    with storage.open_db(root) as connection:
        receipts = storage.list_agent_evidence_receipts(connection, turn_id)
    return {
        reference_id(item["source_id"], item["locator"]): {
            "source_id": item["source_id"],
            "locator": item["locator"],
        }
        for item in receipts
    }


def add_reference_ids(value: Any) -> Any:
    if isinstance(value, list):
        return [add_reference_ids(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: add_reference_ids(item) for key, item in value.items()}
    if isinstance(value.get("source_id"), str) and isinstance(value.get("locator"), str):
        result["ref_id"] = reference_id(value["source_id"], value["locator"])
    return result


def expand_decision(raw: Any, available: dict[str, dict[str, str]]) -> Any:
    if not isinstance(raw, dict):
        return raw
    expanded = copy.deepcopy(raw)

    def expand(ref: Any) -> Any:
        if not isinstance(ref, dict) or "ref_id" not in ref:
            return ref
        ref_id = ref.get("ref_id")
        if not isinstance(ref_id, str) or ref_id not in available:
            raise AgentError(
                "unknown_reference_id", "未取得当前 Turn 回执的 ref_id，不能引用", exit_code=2
            )
        if set(ref) - {"ref_id", "relation", "source_id", "locator"}:
            raise AgentError(
                "invalid_reference_id", "ref_id 引用仅允许 ref_id 与 relation", exit_code=2
            )
        if any(
            key in ref and ref[key] != available[ref_id][key] for key in ("source_id", "locator")
        ):
            raise AgentError(
                "invalid_reference_id", "ref_id 与 source_id/locator 不一致", exit_code=2
            )
        return {**available[ref_id], "relation": ref.get("relation", "supports")}

    if isinstance(expanded.get("evidence_refs"), list):
        expanded["evidence_refs"] = [expand(ref) for ref in expanded["evidence_refs"]]
    if isinstance(expanded.get("claims"), list):
        for claim in expanded["claims"]:
            if isinstance(claim, dict) and isinstance(claim.get("evidence"), list):
                claim["evidence"] = [expand(ref) for ref in claim["evidence"]]
    return expanded


def repair_message(error: Exception, available: dict[str, dict[str, str]]) -> str:
    code = error.code if isinstance(error, AgentError) else "invalid_json"
    detail = (
        error.message[:700]
        if isinstance(error, AgentError)
        else "不是符合 final/ask_user 契约的 JSON 对象"
    )
    refs = [{"ref_id": key, **value} for key, value in list(available.items())[-12:]]
    while (
        refs
        and agent_memory.estimate_input_tokens(
            [
                {"content": json.dumps(refs, ensure_ascii=False)},
            ]
        )
        > 1200
    ):
        refs.pop(0)
    return (
        f"上次回答未通过校验，错误 {code}：{detail}。\n"
        "这是一次仅修复回答的机会，不调用工具、不重新查询；保留已取得事实，修正 JSON/引用，"
        "无法支持的内容移入 gaps。可用紧凑引用如下（仅列部分，不等于全部回执）："
        + json.dumps(refs, ensure_ascii=False, separators=(",", ":"))
        + '\n引用可写 {"ref_id":"实际返回的ID","relation":"supports"}；不要猜 locator。'
    )
