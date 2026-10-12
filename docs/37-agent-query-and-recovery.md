# 字段检索、工作清单与计算错误恢复

2026-10-11。本阶段离线完成，对应 [详细计划](36-calculation-tool-next-plan.md) 的 P0 和用户前置要求的 D2 子集。默认仍是 legacy，Pi 仍为预览；没有增加真实模型执行额度。

## 1. 本阶段解决什么问题

把 Agent 想成一个查资料的助手。此前它可能先猜“ROE 应该在经营效率里”，在那里翻目录，再去财务报表找。拿到数据后，原来的排序代码又碰到了计算工具的限制；原样重试被阻止，整次问答结束。

本轮做了三件连在一起的事：教它先按指标名跨数据集找字段；由程序整理一张工作清单；让可纠正的计算错误有具体反馈和有限继续机会。沿用上一阶段的共用 Python 执行层，两种运行时使用同样的业务规则。

## 2. 实际数据流

```text
问题 + 当前 Turn 获准来源
  → 字段位置未知：query_company_data(mode=fields, query=指标名)，不指定 dataset
  → 核对准确字段、原值/同比、期间 → SQL 选择最小输入，保留 fact_id
  → analysis_id → 受限计算 → 有引用的临时回答
                         ↘ 错误 → 具体反馈 → 修改后再执行 / 用已有事实说明缺口

每次工具成功/错误/blocked
  → Python 保存事件及紧凑观察 → 重建本 Turn 工作清单
  → 注入下一次 legacy/Pi 模型上下文；重启后也从同一记录恢复
```

已知准确字段或用户限定数据集时可以直接查，不强制多走一次目录。这里的“跨数据集”只搜索当前 Turn 已授权、已归档的数据，不是自动搜索整个互联网。

## 3. 文件和函数怎样配合

| 位置 | 实际职责 |
| --- | --- |
| [Skill](../skills/analysis-workspace/SKILL.md)、[agent_policy.py](../src/wonder_stockresearch/interactive/agent_policy.py)、[tool_registry.py](../src/wonder_stockresearch/interactive/tool_registry.py) | 分别提供任务步骤、公共纪律和工具能力说明。位置未知先跨数据集找字段，不能凭指标名补单位。 |
| [agent_company.py](../src/wonder_stockresearch/interactive/agent_company.py) 的 `query()` | 沿用同义词匹配；字段目录最多展示五个期间，并给 `period_count/latest_period/earliest_period/periods_omitted`。数值详情仍从 facts 或 SQL 读取；短 facts 导航保留原形式以节省上下文。 |
| [agent_working_record.py](../src/wonder_stockresearch/interactive/agent_working_record.py) 的 `summarize_result()/build()/project()` | 从当前 Turn 的真实工具记录生成清单；缩短样本，保留输入定位和当前错误。不是让另一个模型写总结。 |
| [agent_execution.py](../src/wonder_stockresearch/interactive/agent_execution.py) 的 `prepare_tool()/execute_tool()/restore_tool_state()/finish_tools()` | 统一成功缓存、错误计数、一次重复纠正反馈、无工具收尾及重启恢复；blocked 不执行工具。 |
| [query_workspace.py](../src/wonder_stockresearch/interactive/query_workspace.py) 的 `validate_function()/run_analysis_function()` | 检查 Python 语法和受限排序 Lambda，定位错误；运行前记录实际执行额度，保存结果与失败产物。 |
| [agent_results.py](../src/wonder_stockresearch/interactive/agent_results.py) | `ToolFailure` 包含错误码、节点、行/列、是否执行、analysis_id 和纠正提示；模型只得到有界结果。 |
| [legacy_agent.py](../src/wonder_stockresearch/interactive/legacy_agent.py)、[agent_context.py](../src/wonder_stockresearch/interactive/agent_context.py)、[agent_memory.py](../src/wonder_stockresearch/interactive/agent_memory.py) | legacy 后续请求及恢复首轮注入已有清单；压缩旧交互时保留受保护的清单和最新结果。 |
| [pi_agent.py](../src/wonder_stockresearch/interactive/pi/pi_agent.py)、[pi_attempt.py](../src/wonder_stockresearch/interactive/pi/pi_attempt.py)、[pi_protocol.py](../src/wonder_stockresearch/interactive/pi/pi_protocol.py) | Pi 读取同一清单/恢复状态；原生 schema 拒绝也纳入公共计数；收尾通过已有控制信号去掉模型工具。没有修改 TS 包装。 |
| [agent.py](../src/wonder_stockresearch/agent.py)、[chat.py](../src/wonder_stockresearch/chat.py) | Agent 开始执行前回调保存 chat 的 Session/Turn 关联；失败后仍可查看和重试原 Turn；`ask-status` 展示清单。 |

工作清单大致如下，数据为虚构示意：

```json
{
  "objective": "分析四期 ROE",
  "completed": ["已取得查询输入；使用现有产物，不重复采集"],
  "available_input": {"analysis_id": "当前Turn的ID", "row_count": 4},
  "last_error": {"code": "analysis_function_not_allowed", "executed": false, "line": 2},
  "pending": ["先处理 last_error；不能把执行失败当成数据缺失", "形成回答，未完成部分写 gaps"]
}
```

程序只保留少量期间/数值/单位和引用样本，完整输入仍在受控产物中。字段目录不是证据；清单也不授予来源权限。成功回答后，状态输出的 pending 清空；这不表示答案的研究质量已被证明。

## 4. 最小可运行例子

先跑零网络的复盘用例：

```bash
./.venv/bin/python -m pytest -q tests/test_agent_recovery_working_record.py
```

该文件用虚构资料和脚本模型跑两种真实循环，经历“跨数据集找到字段 → SQL → 不支持的方法 → 同参重试被阻止 → 改成允许的排序 → 有引用的回答”。数据和数据库放在 pytest 临时目录，不改项目主库，不调用 DeepSeek/OpenD。

正常使用不需要新增开关。配置好模型、已有公司归档后，可将下列 JSON 保存为 `data/roe-question.json`：

```json
{"question":"分析腾讯近四年的 ROE 变化","context":"company_latest","allow_network":false}
```

```bash
./.venv/bin/wonder-research --root . ask HK.00700 --input data/roe-question.json
```

这条 **ask 会真实调用模型，本轮未运行**。它沿用现有会话、事件和 `data/tmp/analysis/` 产物布局，成功后返回临时报告，不生成正式研究报告。用输出中的 ID 替换以下占位符：

```bash
./.venv/bin/wonder-research --root . ask-status SESSION_ID
./.venv/bin/wonder-research --root . agent-trace SESSION_ID
./.venv/bin/wonder-research --root . ask-retry SESSION_ID TURN_ID
```

status/trace 只读；retry 会调用模型，继承原 Turn 来源和剩余预算。chat 首次交互失败也保存原 ID，输入“重试”走这个恢复入口。若预算已经耗尽，不会因此获得新额度。

## 5. 为什么这样改

工作清单相当于一张由程序填写的便签。它会进入下一次给 LLM 的上下文，但内容依据工具执行记录产生，不能任由模型声称“已经查过”。所有 Turn 有各自清单，follow-up 不直接沿用上一题的证据回执。原可见历史继续保存审计。

12 个允许函数仍为 `abs/enumerate/float/int/len/max/min/range/round/sorted/str/sum`。执行环境和工具说明从同一允许列表产生，避免两份名单漂移。新增支持的常见写法是：

```python
def analyze(rows):
    ordered = sorted(rows, key=lambda row: row['period'])
    return {'samples': [
        {'period': row['period'], 'value': row['value_decimal'], 'unit': row['unit']}
        for row in ordered
    ]}
```

Lambda 仅允许直接出现在 `sorted` 的 `key`：一个位置参数，读取该参数的字段、常量、元组和基础运算，可用 `float/int/str` 转换。外部变量、方法、嵌套 Lambda、其他调用都拒绝。AST 是代码的语法结构；检查父节点和参数作用域才能开放这一个用法，不能简单允许所有 Lambda。期间字符串仍需符合可排序格式，程序不自动猜财年。

错误恢复的实际规则：

1. 首次可纠正错误返回具体反馈。语法拒绝不执行函数，不消耗三次实际函数执行额度；已有 SQL 输入保留。
2. 原样提交已失败请求时阻止执行，但每 Turn 首次提供一次额外纠正反馈，允许模型修改。成功重复调用也受原有有限缓存规则约束。
3. 再次耗尽重复纠正机会，或本 Turn 累计三次可恢复工具错误，进入无工具收尾；无关成功调用不清零计数。剩最后一次模型决策时也先收尾。
4. 收尾用已有事实回答，未完成部分写 gaps。若模型仍违反无工具/引用/协议规则，或预算不足，仍会失败；程序不会伪造成功或突破预算。
5. 重启/retry 从事件恢复错误与纠正计数。实际函数运行失败也占执行额度；跨 Turn、损坏来源和未授权访问仍直接拒绝。

默认错误观测只含安全摘要。真正工具失败记为失败 span；执行前阻止记为 `tool.blocked` guardrail，带 `executed=false/outcome=blocked`，不能把“保护生效”误读成工具已经执行成功。

## 6. 实际验证与不能证明的事

2026-10-11：默认全量 **404 passed（8.29 秒）**；Pi 全量 **404 passed（12.66 秒）**。新增文件提供 36 项用例，覆盖三类允许排序、拒绝边界及位置、12 函数可用、失败执行额度、跨数据集中文/英文检索和目录分页、旧交互压缩后继续计算、Turn 隔离、恢复首轮保留输入/错误、重启纠正计数、无关成功不重置错误、预算内有限收尾、失败 chat 关联和原 Turn 重试、Pi 原生参数拒绝。

```bash
./.venv/bin/python -m pytest -q
WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q
./.venv/bin/ruff check src tests scripts
```

Ruff lint 通过。第一次全量发现 facts 短目录被新增摘要挤掉，已保留其原形式，原断言通过；没有弱化既有测试。最终复核另补上 legacy 恢复首轮的清单注入及 chat 重试原话保留，重新全量通过。旧权限、引用、预算和冻结回归同时通过。打包和文档检查的实际结果见 [PROGRESS](../PROGRESS.md)。

离线脚本能证明路径可走通，不能证明真实 LLM 必然选对字段、遵守步骤或解释正确。这不是对原 trace 的重新运行，也没有改变原 30 题结果。

## 7. 一个小练习

阅读新增测试中的两种写法：`row.get('period')` 和 `row['period']`。前者涉及方法调用，会在执行前被拒绝；把排序 key 改成后者，且字段确实存在时，会通过。预期 SQL analysis_id 保留，语法拒绝不占实际运行额度，最终成功后清单不再显示当前计算错误。

另可将虚构字段的单位设为 null：预期依旧保留 unknown，不补 `%`。字段名看起来熟悉不能代替来源证据。

## 8. 已知限制与下一步

字段检索顺序由提示词/Skill 引导，没有硬编码 ROE 路线；仍需真实模型同快照评测。清单有大小上限，样本和引用可能明确省略，不能承诺无限记忆。12 个函数加语法/进程限制不是完整操作系统安全沙箱。

公司展开视图（D1）、SQL 下推/快照复用（D2 剩余部分）、指标口径/单位映射与结构化数字回答（D3）仍待实施。事实归属仍经 `batch_id → source_batches.ticker`，本轮没有给 company.db 增加视图或列。原 Pi 40/40 额度不重置，真实收益未验证。本教学阶段完成后暂停。
