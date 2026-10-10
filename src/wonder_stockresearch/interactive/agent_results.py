"""两种 Agent 运行时共用的工具结果和模型投影。

完整业务结果用于证据、缓存和产物保存；模型只接收有界 JSON 文本。
这里不执行工具、不授予权限，也不改变最终报告的数据模型。
"""

from __future__ import annotations

import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wonder_stockresearch.interactive import agent_memory


class ToolFailure(BaseModel):
    """受控工具错误；只有执行层可以决定是否允许模型纠正后继续。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    next_step_hint: str | None = None
    recoverable: bool = False


class ToolOutcome(BaseModel):
    """内部结果外壳，data 保留完整原值，不直接发往 Node 或模型。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
    error: ToolFailure | None = None

    @model_validator(mode="after")
    def check_status(self) -> Self:
        if (self.status == "error") != (self.error is not None):
            raise ValueError("error 状态必须带错误；success 状态不能带错误")
        return self

    @property
    def model_text(self) -> str:
        """保持既有模型工具 payload，不向模型暴露内部结果外壳。"""
        view = self.data
        if self.error is not None:
            view = {
                **view,
                "code": self.error.code,
                "message": self.error.message,
                "is_error": True,
                "next_step_hint": self.error.next_step_hint or recovery_hint(self.error.code),
            }
        return model_projection(view)


def model_projection(result: dict[str, Any], *, token_limit: int = 1500) -> str:
    """按完整事实裁剪模型内容；结果与证据回执由执行层保留。"""
    view = dict(result)
    if (
        isinstance(view.get("result"), dict)
        and "function" in view["result"]
        and isinstance(view["result"].get("sql"), dict)
        and "rows" in view["result"]["sql"]
    ):
        # 分析产物中的完整 SQL 行已经单独归档，展示时优先函数结果。
        artifact = view["result"]
        view["result"] = artifact["function"]
        view["input_summary"] = artifact.get("input_summary", {})
        view["sql_rows_archived"] = True
    for key in ("steps", "draft_sections"):
        if isinstance(view.get(key), (dict, list)):
            view[key + "_summary"] = {"count": len(view.pop(key))}
            view["context_truncated"] = True
    # 财务单值不能切半；公司业务长文可以提供明确标记的摘录。
    for key in ("records", "rows"):
        if not isinstance(view.get(key), list):
            continue
        projected = []
        for original in view[key]:
            item = dict(original)
            for field in ("value", "value_json", "text"):
                value = item.get(field)
                encoded_json = field == "value_json"
                if encoded_json and isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        continue
                if isinstance(value, str) and len(value) > 1200:
                    excerpt = value[:1200] + "…（摘录，正文已截断）"
                    item[field] = (
                        json.dumps(excerpt, ensure_ascii=False) if encoded_json else excerpt
                    )
                    item[field + "_truncated"] = True
                    view["context_truncated"] = True
            projected.append(item)
        view[key] = projected
    if view.get("mode") == "facts" and view.get("records"):
        catalog = view.pop("field_catalog", [])
        view["field_catalog_omitted"] = len(catalog)
        view.pop("sql_tables", None)
    if isinstance(view.get("sources"), list):
        # 取得日期是公司基本信息题的有效输出字段。
        view["source_dates"] = [
            {"source_id": item.get("source_id"), "retrieved_at": item.get("retrieved_at")}
            for item in view["sources"][:16]
        ]
    encoded = agent_memory.tool_context(view, token_limit=token_limit)
    projected = json.loads(encoded)
    if "total_matches" in projected and isinstance(projected.get("records"), list):
        count = len(projected["records"])
        projected["returned_records"] = count
        next_offset = int(projected.get("offset", 0)) + count
        projected["has_more"] = next_offset < projected["total_matches"]
        projected["next_offset"] = next_offset if projected["has_more"] else None
    encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
    if agent_memory.estimate_input_tokens([{"content": encoded}]) <= token_limit:
        return encoded
    # 单个嵌套对象也可能超限；返回导航，不能把巨大单项原样放入上下文。
    navigation = {
        key: projected[key]
        for key in (
            "analysis_id",
            "status",
            "source_id",
            "locator",
            "mode",
            "total_matches",
            "offset",
        )
        if key in projected
    }
    if projected.get("is_error") is True:
        # 错误消息过大时也保留失败身份，不能把纠正提示变成一次空查询。
        navigation["is_error"] = True
        if "code" in projected:
            navigation["code"] = projected["code"]
        navigation["next_step_hint"] = recovery_hint(str(projected.get("code", "")))
    navigation.update(
        context_truncated=True,
        returned_records=0,
        next_step="结果超过本次展示预算；按字段/期间缩小查询，或分段读取来源/分析产物。",
    )
    if navigation.get("is_error") is True:
        navigation["next_step"] = (
            "错误正文超过本次展示预算；按 next_step_hint 处理，不将错误视为资料不存在。"
        )
    if len(str(navigation.get("locator", ""))) > 160:
        navigation.pop("locator")
        navigation["locator_omitted"] = True
    if "total_matches" in navigation:
        navigation.update(has_more=True, next_offset=navigation.get("offset", 0))
    return json.dumps(navigation, ensure_ascii=False, separators=(",", ":"))


def recovery_hint(code: str) -> str:
    """现有受限查询/函数工具共用的纠正提示，不改变错误的可恢复分类。"""
    if code == "analysis_sql_limit":
        return "本 Turn 的 SQL 调用次数已耗尽；使用已取得的产物完成计算/回答，未完成部分写 gaps，不再尝试 SQL。"
    if code in {
        "analysis_output_too_large",
        "analysis_sql_row_limit",
        "analysis_sql_timeout",
        "analysis_sql_failed",
    }:
        return (
            "不要 SELECT *；先用字段目录确定 field_key，再限定 period_kind/period，"
            "只 SELECT fact_id,period,value_decimal 等必要列。SQL 超步数时减少扫描和排序；不能推断没有数据。"
        )
    if code == "analysis_function_failed":
        return (
            "临时函数执行失败；用 read_analysis_artifact 检查原 analysis_id 的输入。"
            "失败产物不能原位重跑函数；缩小/纠正 SQL 取得新产物后改写函数，不把失败当作没有数据。"
        )
    if code == "analysis_function_not_allowed":
        return (
            "函数仅支持 def analyze(rows) 与允许的内置函数；不支持属性/方法调用（如 .values()/.endswith()）。"
            "用 rows 遍历及 row['period'] 下标；期间筛选尽量放到 SQL。保留已成功查询的 analysis_id。"
        )
    return "保留已取得的事实与产物，纠正参数或缩小查询；执行失败不等于资料不存在。"
