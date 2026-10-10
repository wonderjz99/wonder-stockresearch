---
name: interactive-research-agent
description: 独立回答公司开放问题、生成临时报告；用户明确保存时才创建 Memo。触发词：临时分析、风险、反方、备忘录。
---

# 交互式公司研究

同一个 Python Agent 回答当前问题并生成 InteractiveReport，不修改正式 ResearchRun、评分、门闸或报告。

- 标准 Q01–Q24 研究/复评交给 company-research；更新评分需新建固定研究。
- 通用、反方、宏观、同行、产能和库存是分析焦点，不是多个 Agent；缺少专用数据时说明缺口。
- 可读取归档资料、正式报告和 Memo。analysis-workspace 授权后可补取当前公司的富途只读财务资料；allow_network=false 禁止补取。不能访问账户/交易接口或任意网络。
- 临时补取独立归档，不执行 collect、save-draft 或 finish；只有用户明确要求 memo-save 才保存公司观察。
- 引用真实工具返回的 source_id/locator，不把工作记忆当证据，不生成投资建议。

独立终端入口：wonder-research ask <ticker> --input question.json。项目程序读取 DEEPSEEK_API_KEY；联网补取需已配置 OpenD，外部 Agent host 不是依赖。实现依据见 docs/09-agent-layer.md 与 AGENTS.md。
