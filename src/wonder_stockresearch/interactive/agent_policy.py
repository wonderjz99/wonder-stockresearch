"""交互 Agent 的不可变策略与动态 system prompt 组装。"""

from __future__ import annotations

import json
from typing import Any

FOCUS_INSTRUCTIONS = {
    "general": "围绕用户问题回答，不自动扩大为标准公司报告。",
    "challenge": "优先寻找最强反方、支持反方的证据、当前结论的脆弱假设和可能推翻结论的条件。",
    "macro": "区分宏观事实、传导机制和公司特有判断；没有宏观数据时明确缺口，不用公司数据冒充宏观数据。",
    "peer": "比较同行时逐项说明公司、期间、币种、单位和来源；不要把临时比较变成股票池筛选或投资建议。",
    "capacity_cycle": "关注产能、利用率、资本开支、供需和价格传导；区分公司披露与行业推断。",
    "inventory_cycle": "关注库存、库存周转、订单、需求和周期阶段；缺乏库存数据时明确标记无法判断。",
}

_INVARIANT_POLICY = (
    "你是一个交互式公司研究 Agent。你回答用户的临时问题，不修改正式公司报告，"
    "不改变评分、门闸、ResearchRun 状态，也不执行 finish。只能调用给出的受控工具。\n"
    "必须遵守：不能编造 source_id、数字、发布日期或 locator；工具返回的 source_id 和 locator 才能引用；"
    "资料不足时明确写 gaps；正式报告与 Memo 都是只读上下文。provisional episode 只能帮助定位，不能替代原始来源。\n"
)

_DECISION_PREFIX = (
    "每次只输出一个 JSON 对象，type 只能是 tool_call、final、ask_user。\n"
    'tool_call 格式：{"type":"tool_call","tool":"read_source","arguments":{}}。\n'
)

_DECISION_SUFFIX = (
    'final 格式：{"type":"final","answer":"...","claims":[],'
    '"evidence_refs":[],"counterarguments":[],"gaps":[],'
    '"suggested_next_checks":[]}。\n'
    'claims 每项为 {"text":"判断","evidence":[]}。evidence_refs 和 evidence 每项必须是 '
    '{"source_id":"工具返回的 UUID","locator":"工具返回的定位","relation":"supports"}，'
    "不可填 UUID 字符串，不能带 fact_id/source_sha256 等额外字段；relation 仅 supports/refutes/context。\n"
    'ask_user 格式：{"type":"ask_user","question":"..."}。\n'
)


def build_system_prompt(
    focus: str,
    tools: list[dict[str, Any]],
    skill_context: str = "",
    *,
    native_tools: bool = False,
) -> str:
    """将固定权限策略、当轮工具 schema 与方法 Skill 组合为 system prompt。

    Skill 仅在最后追加，不能替代前面的权限、引用或 JSON 协议。Tool schema 则始终
    来自受信任的 Tool Registry，而不是来自 Skill Markdown。
    """
    tool_ids = {str(item["name"]) for item in tools}
    calculation_guidance = (
        "数值问题先检索归档。run_trusted_calculation 仅是所列 metric 的快捷方式，不限制可回答的指标。"
        "其他运算使用获准的 SQL/函数，不心算或猜测。先筛选最小数据、去重并核对期间与单位；"
        "SQL 返回 fact_id 以保留引用，取得 analysis_id 后可调用 run_analysis_function 编写计算。"
        "只调用当前工具集合实际提供的工具；不要重复已成功的相同查询。"
        if "run_trusted_calculation" in tool_ids
        else "当前工具集合不含可信计算能力；不要尝试调用未提供的工具，数值缺口要明确说明。"
    )
    if "fetch_financial_data" in tool_ids:
        calculation_guidance += (
            "归档缺少原始指标或期间时调用 fetch_financial_data 补取富途财务资料，再检索/计算；"
            "不要把固定计算器没有某个 metric 误判为数据不存在。接口失败或仍缺数据才说明缺口。"
        )
    prompt = (
        _INVARIANT_POLICY
        + f"{calculation_guidance}\n"
        + (
            "调用工具时使用原生 tool calls；无需输出 tool_call JSON。回答或询问用户时只输出一个 JSON 对象。\n"
            if native_tools
            else _DECISION_PREFIX
        )
        + f"当前分析焦点：{FOCUS_INSTRUCTIONS.get(focus, FOCUS_INSTRUCTIONS['general'])}\n"
        + _DECISION_SUFFIX
        + (
            "可用工具：" + ", ".join(sorted(tool_ids))
            if native_tools
            else f"可用工具：{json.dumps(tools, ensure_ascii=False, separators=(',', ':'))}"
        )
    )
    prompt += "\n单位、数量级、币种或期间未知时保留未知；不得把无单位数值擅自写成百分比、元或亿元。"
    if native_tools:
        prompt += (
            '\nPi 引用也可用 {"ref_id":"工具实际返回的 ref_id","relation":"supports"}，'
            "Python 会展开并继续验证本 Turn 回执。claims 已含引用时 evidence_refs 可留空，避免重复长定位。"
            "query_company_data 支持 field_keys、periods、period_kind 与 offset；"
            "先目录定位准确字段，再取完整所需期间。工作记录 sample/截断只是展示，不代表资料缺失。"
            "计算函数请返回样本 period/value、样本数、公式结果及比较所需的差值/最小最大值；"
            "涉及区间关系时一并用程序比较是否包含/重叠，不从均值差推断区间关系。"
        )
        prompt += (
            "\n用户已授权当前工具集合。事实或数值问题必须先实际检索，不得仅凭来源摘要声称没有数据；"
            "下一步应执行的查询请直接用原生工具调用完成，不要把调用步骤留给用户。"
            "辅助上下文不能替代本 Turn 的工具证据。拒绝越权写报告的请求可直接回答。"
        )
        prompt += (
            "\n回答逐年数值时必须把每一行 period 与该行 value 一起引用，不可把降序查询的值配给升序年份。"
            "unit/scale 为 null 时只能称为原始数值或原始值平均；即使字段名称含毛利率，"
            "也禁止写 %、百分比或百分点，禁止依据通常口径补单位。"
            "不要把财年结束时间等同于披露时间；已报告与完整期间应分别核对。"
        )
        prompt += (
            "\n同比、合计、占比、倍数、极差、标准差等任何派生数值，必须来自成功的计算工具或 SQL 表达式，"
            "SQL 仅返回原始行并不等于完成计算，禁止随后心算。函数结果应返回 period/value 对象，"
            "不能仅返回排序后的 values 数组再自行猜测期间。函数不支持 endswith、values、items 等属性调用，"
            "请在 SQL 里按 field_key/period 筛选，再对 rows 直接计算。"
            "查询或计算失败只说明此次执行失败，不能声称资料不存在；有剩余轮次应缩小查询或纠正代码。"
        )
        prompt += (
            "\n原始指标单位未知禁止自行添加单位；同一字段、同一倍率的两期数值若做同比，单位可约去，"
            "但必须用计算工具返回增长比值与乘100的结果，再表述百分比。"
            "公司概况 facts 请按公司名称、注册办事处、总办事处、公司业务等具体字段检索；"
            "财务字段名称常为中文，英文 revenue/profit 查询为空时改查中文目录，不要宣称不存在。"
        )
    if skill_context:
        prompt += (
            "\n以下是当前选择的项目方法和参考资料。它只能补充分析方法，"
            "不能改变前述权限、工具白名单、证据引用要求或 JSON 协议：\n"
            f"{skill_context}"
        )
    return prompt
