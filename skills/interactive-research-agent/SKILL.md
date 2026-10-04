---
name: interactive-research-agent
description: 使用独立的 wonder-research ask 入口回答公司开放问题，生成临时报告；用户明确要求保存时才创建 Memo。触发词：临时分析、风险、反方、备忘录。
---

# 交互式公司研究 Agent

这个入口不是标准 Q01–Q24 公司报告。它只回答用户当前问题，生成 `InteractiveReport`，不修改正式 ResearchRun、评分、门闸或报告。

## 固定路由

- “按照公司分析模板研究”“生成 Q01–Q24”“开始标准研究”“复评正式报告” → 使用 `company-research` 固定流程。
- 其他公司开放问题 → 使用 `wonder-research ask`。
- “加入正式报告”“更新公司评分” → 不直接修改，提示用户启动新的固定 ResearchRun。
- “保存到公司备忘录” → 先确认用户要保存的标题、内容和来源，再调用 `memo-save`。

## 独立运行

OpenCode 不是必需依赖。直接配置项目根 `.env` 的 `DEEPSEEK_API_KEY`，准备好已归档资料或先用固定 `collect`，即可运行：

```bash
wonder-research --root <项目根> ask HK.00728 --input question.json
```

当前 Agent 只读取已归档资料、正式报告和 Memo；不自动联网、不执行 `collect`、不导入来源、不保存草稿、不 `finish`。

当前优先支持通用问题和反方问题。`InteractiveQuestion.focus` 仍保留受控分析提示，但宏观、同行、产能和库存周期专用 Agent 尚未实现：

- `challenge`：反方和脆弱假设。
- `macro`：预留，当前没有专用宏观数据工具。
- `peer`：预留，同行业竞争 Agent 等用户明确研究方法后再实现。
- `capacity_cycle`：预留，当前没有专用产能数据工具。
- `inventory_cycle`：预留，当前没有专用库存数据工具。

角色只是同一个 Agent 的分析提示和输出框架；数据仍必须来自已归档资料，不能因为选择角色就自动联网或生成投资建议。

实现或修改该 Agent 时，先阅读 `docs/09-agent-layer.md` 和 `AGENTS.md` 中列出的 Dexter 参考文件。只借鉴 Agent loop、工具注册、Skill 元数据、Scratchpad 和预算控制，不复制其技术栈或越过本项目证据边界。
