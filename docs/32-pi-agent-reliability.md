# Pi 问答可靠性修复：让模型拿到合适的数据

2026-10-09。上一轮真实验收为4通过、5部分、21失败，本阶段针对已观察到的工具/上下文缺陷修复。用户确认暂不开 thinking；没有新增模型调用，也没有切换默认运行时。这里只记录实际离线能力，不把测试通过等同于研究质量通过。

## 解决什么

Q01执行36在多轮调用后只剩10,147 tokens，下一轮估算输入却有20,865。状态工具携带步骤和草稿正文是重要原因。另一些问题把预览没显示的年份当作缺失，或引用错误后重查整个问题。Q29的计算正确但区间解释错误，说明计算结果应提供清楚的比较依据；这次没有加入另一个模型审查器。

```text
问题 JSON → Session / Turn 固定预算 → Pi 原生循环
                                    ↓
         Python 查询：准确字段/期间/分页 → 证据回执 + ref_id
                                    ↓
         可见历史 → 工作记录 + 有界上下文 → 下一次模型调用
                                    ↓
         final → ref_id 展开 → 原证据校验 → 不可变临时报告
                    校验失败 → 一次无工具的回答修复
```

## 一次问题如何执行

1. `models.InteractiveQuestion` 的新默认是60,000累计tokens和10轮。`agent._insert_turn` 将累计预算、单轮输入/输出上限一起写进Turn的budget_json。显式输入30,000/6仍有效；已有Turn的retry/respond不改变预算，follow-up继承原Session配置。
2. `agent_tools._execute_tool_impl` 的状态工具直接将steps和draft_sections换为计数。公司资料沿用`query_company_data`，在`agent_company.query`内按来源固定范围筛选，没有新增越权数据入口。
3. `pi_context.tool_projection`先保留事实，再省略重复目录。投影后重新计算returned_records和next_offset，因此模型分页不会跳过被上下文省略的行。单个巨大嵌套对象也只返回明确的截断导航，不能无界放入请求。
4. `pi_context.working_memory`从当前Turn的可见工具调用/结果重建最近操作，保留参数、少量事实、analysis_id、计算结果和分页状态。它是可恢复的定位记录，不是模型思维链，也不是新的事实来源。原始可见审计不改写。
5. `pi_evidence.catalog`仅从当前Turn的回执生成稳定ref_id。模型可以在claims的evidence中使用短ID，Python先展开再经过原Pydantic、公司/来源范围和回执检查；对外报告仍保存source_id/locator。其他Turn的ID、伪造ID或ID与定位冲突都会拒绝。
6. 如果final结构/引用失败，Python返回具体错误及部分合法引用。`runner.mjs`在该修复轮移除tools，Python也拒绝修复轮的工具调用；最多一次，仍计入原预算，避免Q01式重查。
7. `query_workspace.run_analysis_function`在结果和归档中附上实际SQL输入的字段/期间/单位覆盖摘要及代码哈希。模型仍需写计算函数，系统提示要求返回样本、公式结果、差值和必要的区间比较。程序没有替任意问题自动选择财务口径，也不能保证模型正文永远正确。

## 配置与最小示例

Pi通过环境变量显式启用，安装方式见[运行时说明](30-pi-agent-runtime.md)。问题文件可写为：

```json
{
  "question": "读取中国移动2023至2025完整财年的毛利率原始值，并计算平均值。",
  "context": "company_latest",
  "allow_network": false,
  "max_iterations": 10,
  "token_budget": 60000,
  "input_token_limit": 16000,
  "output_token_limit": 4096
}
```

由用户保存为`data/question.json`后可运行（本阶段没有执行此真实问答）：

```sh
export WONDER_AGENT_RUNTIME=pi
./.venv/bin/wonder-research --root . ask HK.00941 --input data/question.json
```

input_token_limit允许1024–16000；output_token_limit允许64–8192。这两项是Pi单轮策略，旧Python loop仍沿用原单轮策略。单轮输入是启发式估算的硬守卫，不是服务端精确tokenizer；超过上限在请求前返回agent_context_too_large，累计预算不足仍返回agent_budget_exhausted。动态压缩目标会给输出留空间，但规则、原问题与最新完整工具组无法继续缩小时可能超过目标；最终仍必须通过硬上限与剩余额度检查。新真实请求的context_budget事件记录输入估算、剩余额度和修复模式。

查询工具参数示例（模型原生调用，不是新的CLI命令）：

```json
{
  "dataset": "statements",
  "mode": "facts",
  "field_keys": ["从 fields 目录取得的真实字段ID"],
  "periods": ["2023/FY", "2024/FY", "2025/FY"],
  "period_kind": "annual",
  "offset": 0,
  "max_records": 20
}
```

query字段仅做有限中英文同义词扩展，例如revenue命中营业收入，capex可找到购买资产候选；它不证明命中科目都属于同一资本支出口径。仍需读label、维度、单位和原始来源。没有引入向量检索或问题专用公式。

## 离线验证与练习

本阶段实际运行完整Pi与默认路径回归，结果记录在PROGRESS。新增的`tests/test_pi_reliability.py`覆盖大状态、年度/准确字段筛选、分页不漏行、工作记录、巨大单项、引用修复不重查、跨Turn引用拒绝、预算继承、单轮前置停止及计算输入/区间关系。`tests/test_pi_runtime.py`的真实Pi SDK加本地fetch替换还验证修复轮不发送工具、thinking保持disabled；不访问网络。

执行36的真实历史消息另外做了纯离线投影检查：当前策略/工具定义下估算输入7,768，低于当时剩余10,147。元数据保存在忽略目录`data/audits/20261009-pi/offline-context-recheck.json`。历史问答、质量评级及40次执行账本没有被修改。

练习：在分页测试中降低token_limit，预期每页展示变少，但最终收集到的fact_id顺序与全集一致；把引用ID换成另一个Turn的ID，预期在生成报告前拒绝。

## 限制与下一步

thinking仍关闭，未更换模型，未增加多Agent。当前工作记录为最近操作的有界摘要，复杂任务仍可能需要重读产物。未知单位/披露日不补猜；同义词检索不解决经济口径。必要检索前提前final、错误文字解释、事实源本身异常等真实质量问题尚未证明解决。

原40次真实额度已耗尽。本阶段新增真实执行0；下一步需另行授权限额后，在同一数据快照上分别比较工具修复与预算放宽的效果，再完整回归30题。质量未通过前保留旧默认和历史记录。
