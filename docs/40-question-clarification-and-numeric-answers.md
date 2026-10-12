# 问题澄清与可核验的数值回答

## 1. 本阶段解决什么

2026-10-11 用户要求创建类似 grillme 的小 Skill，并把最新五题的问题一起修复。本阶段接续 [五题计划](39-five-question-review-and-general-plan.md)，完成 G1–G3 正确性修复和 G4 离线回归；真实模型五题仍待复测。

旧问题可以理解成：研究员查到了资料，却没有固定“要回答哪几年”和“每个数属于哪一年”，报错后也没有有效纠正。本次增加问题记录，并让程序负责数值部分。

| 五题失败 | 共用处理 |
| --- | --- |
| R01：取错年份，下降写成回升 | 确认目标期间，绑定期间与事实身份，程序生成变化 |
| Q08：查目录绕路，编造定位 | 通用别名推荐候选、原值优先，目录指向 SQL，数值必须有事实回执 |
| Q11：函数拒绝后声称没有计算器 | 错误给具体调用名，常用运算不必写代码，收尾准确说明能力 |
| Q20：排序后年份错配，未知单位写百分点 | 排序移动整条记录；未知单位保留未知，来源明确百分数才用百分点 |
| Q23：季度归档缺失仍重复空查 | 返回实际覆盖，阻止同范围已知无结果的等价查询，保留其他授权路径 |

业务代码没有按公司、ticker、题号或问题原文分支。测试使用虚构公司、不同指标名和可变数值。

## 2. 实际调用图

```text
ask / chat / ask-followup
 → 新 Turn：冻结时钟、方法哈希、权限、预算
 → question_requirements：原话、目标期间、待确认项
 → 必要歧义 → waiting_user → 原 Turn 的新 Attempt
 → 同一 Agent + 澄清 Skill → 字段定位 / 只读 SQL
 → SQL 保存原始 verified_facts + 本 Turn 事实回执
 → final.numeric_blocks：产物 ID、事实 ID、运算名称
 → Python 复核身份/期间/单位，Decimal 计算，渲染表和变化
 → 原引用校验 → 不可变 InteractiveReport
```

chat 保存 `respond_interactive` 待办。下一条补充直接交给 `agent.respond`，不经过 Router，也不另开新目标。完成后的新问题才创建新 Turn；失败时保留实际新 Turn 的重试关联。

## 3. 实际文件与入口

- [SKILL.md](../skills/question-clarification/SKILL.md)：每题检查对象、指标口径、期间、交付目标，仅问影响答案的选择。manifest 是没有工具权限的 method。
- [question_requirements.py](../src/wonder_stockresearch/interactive/question_requirements.py)：`load_method` 加载方法；`resolve` 解析有限相对年、明确年份/季度；`load/save` 从事件及补充恢复要求。记录已处理 Attempt，防止旧“是”确认后来提出的新候选。
- [agent.py](../src/wonder_stockresearch/agent.py)：共用执行入口加载并核验方法哈希，处理确定需要追问的情况。明确年份直接执行，不额外调用一次澄清模型。
- [query_workspace.py](../src/wonder_stockresearch/interactive/query_workspace.py)：按 SQL 返回的 fact_id 反查真实快照，保存 verified_facts；SQL 别名中的伪造年份/数值不能覆盖原记录。AST 拒绝说明具体调用名。
- [numeric_answer.py](../src/wonder_stockresearch/interactive/numeric_answer.py)：`prepare` 复核输入、期间、口径和回执；`calculate` 分组及 Decimal 运算；`render` 输出表和变化。
- [agent_execution.py](../src/wonder_stockresearch/interactive/agent_execution.py)：两种 runtime 共用确认、空查阻止、提交与有界纠正。未决期间允许只读查财年/覆盖，但不执行计算或接收报告。
- [agent_company.py](../src/wonder_stockresearch/interactive/agent_company.py)：跨数据集别名候选、原值优先排序和归档覆盖；别名命中不证明经济口径相同。
- [chat.py](../src/wonder_stockresearch/chat.py)：区分等待补充、失败重试与后续新题，恢复正确 Turn。

要求记录和工具摘要进入已有工作清单，每轮保留已确认要求、产物及错误；完整输入仍留在分析产物中。

## 4. 使用方法

原入口保持可用：

```bash
uv run --extra observability wonder-research --root . chat
```

输入“分析腾讯近四年的 ROE 变化”，若分析日期为 2026 年且没有适用确认，会先提出 2022、2023、2024、2025 完整自然年度候选，同时允许公司财年或其他范围。回复“是”确认所展示候选；回复明确年份可修改范围。不会按数据库最近四条自动替换目标。

直接 ask 也加载同一 Skill。输入放在 UTF-8 JSON，例如使用者创建 `data/inputs/roe-question.json`：

```json
{
  "question": "分析2022至2025财年的ROE变化",
  "context": "company_latest",
  "allow_network": false,
  "as_of": "2026-10-11T12:00:00+08:00"
}
```

```bash
uv run wonder-research --root . ask HK.00700 --input data/inputs/roe-question.json
uv run wonder-research --root . ask-status SESSION_ID
uv run wonder-research --root . ask-respond SESSION_ID TURN_ID --input data/inputs/clarification.json
```

补充文件内容可为 `{"question":"2022、2023、2024、2025财年"}`。示例文件需按需创建；ask/respond 会调用已配置模型，本阶段未运行这些真实命令。as_of 可选，默认 Turn 创建时间，必须带时区；它仅提供分析时钟，不新增历史回测或资料可用性证明。

目标含 2025、数据只到 2024 时，会先询问保留目标列缺口、调整范围还是补取。回复“保留目标，列出缺口”可计算已有期间，但报告必须带缺口；不会拿 2022 替补。自然年度要求遇到非 12 月 31 日期末或期末未知的事实，也会确认，不能直接替代为公司财年。

## 5. 为什么把数值交给程序

模型最终可提交以下结构，ID 必须是真实 SQL 产物 ID，不能使用占位符：

```json
{
  "type": "final",
  "answer": "可核验的结果如下；归档不足以说明经营原因。",
  "numeric_blocks": [{
    "analysis_id": "成功SQL的UUID",
    "fact_ids": [],
    "operations": ["values", "mean", "range", "population_stddev", "changes"]
  }]
}
```

空 fact_ids 表示查询的全部原始数值。程序检查 session/ticker/Turn、来源哈希、原值/同比/环比及事实回执，逐组检查目标年份/季度和要求运算。字段、期间类型、单位、倍率、币种、会计口径及业务维度组成序列身份。单位/倍率不明时按来源分开，不随意跨源拼接；重复期间要求缩小来源/修订版本。

比如原记录为 `2023→14，2024→9，2025→8`，SQL 即使按值返回 `8、9、14`，程序仍移动包含年份和值的整条记录，输出原关系并计算逐期下降，不让模型重新贴年份。

Decimal 运算保留 42 位精度及公式版本；原始值字符串保留，统计量展示取六位小数。未知单位显示“单位未确认”；来源明确百分数才使用百分号/百分点。用户要求“单位用百分点”不等于来源证明。

answer/claims 不再平行维护数值或数值趋势，以免“表正确、正文相反”。模型继续解释有独立证据支持的原因和限制。可纠正的格式、引用、数值提交错误在预算内最多纠正一次，跨 Attempt 持久化，retry 不重置。致命权限/来源错误仍不绕过。

## 6. 实际验证

[新增回归文件](../tests/test_question_clarification_and_numeric_answer.py) 覆盖两种 runtime、补充后候选确认、chat 同 Turn 恢复、排序身份、未知单位、跨源、缺年、原值/同比、年度/季度、统计量、无代码计算、函数拒绝后的纠正、等价空查、别名到真实数值、SQL 别名伪造、跨 Turn 产物、Skill 内容变更和纠正预算。实际命令、数量和结果见 [PROGRESS](../PROGRESS.md)。

[三个 Skill 评测提示](../skills/question-clarification/evals/evals.json) 已保存供后续有/无 Skill 比较，本阶段未执行真实模型 A/B。脚本模型测试验证契约与失败路径，不能证明真实模型的理解、工具选择或原因解释稳定性。

安装包含同源 Skill，没有项目 skills 目录时可加载包内版本。离线构建、文件哈希比对及临时解压隔离加载已验证，未发布/安装到用户环境。未改 TypeScript，因此未重跑 TS 构建。

## 7. 小练习

修改回归文件 `archive()` 虚构数列中两个年份的值，再运行该文件。预期均值可能不变，逐期方向改变，表仍对应各年原值。删除最新年度时应等待确认；允许缺年后才生成带缺口的报告。

## 8. 限制与下一步

真实五题仍需同来源、同预算复测，并记录必要澄清。历史 Pi 40/40 额度不重置；本阶段没有新增模型/OpenD 请求、主库迁移或历史报告改写。

有限解析器识别常见近 N 年、明确年范围及 YYYYQn；复杂财历、滚动窗口和其他自然语言仍由同一 Agent/Skill 澄清。单位、披露时间缺依据仍保留未知，未新增单位映射、向量库或 sub-agent。通用运算目前是上述五种，其他派生公式需扩展可验证产物契约。

D1 公司展开视图、D2 全面 SQL 下推/快照复用/性能测量仍待做。G3 覆盖本次检索和空查正确性，不代表性能项目全部完成。下一步优先真实五题质量验收，再决定数据覆盖或性能改动。
