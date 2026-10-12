# 框架出处与设计依据

2026-10-11 澄清与数值回答阶段复核下列 Dexter 主循环、注册表和 scratchpad 源码，只沿用有限执行、方法元数据和工作记录思路。question-clarification、期间确认、事实身份绑定、Decimal 渲染和回执校验由本项目实现。受用户所说 grillme 的“先问清楚”启发，未复制其 Skill 或新增 Agent；编写使用本机 skill-creator 方法，真实模型 Skill A/B 未运行。见 [本项目实现](../skills/question-clarification/SKILL.md)。

2026-10-11 公司主备忘录/save 阶段沿用并复核下文 Dexter 主循环、工具/Skill 注册和 scratchpad 的边界。主备忘录唯一性、原报告确定性保存、迁移和版本规则为本项目自行实现；保存入口在 Python CLI/chat，未向 Agent 增加写工具或采用外部记忆服务，见 [本项目实现](../src/wonder_stockresearch/interactive/company_memo.py)。

2026-10-11 字段检索/工作清单/错误恢复阶段重新核对用户指定 Dexter 的 [Agent loop](https://github.com/virattt/dexter/blob/main/src/agent/agent.ts)、[工具注册](https://github.com/virattt/dexter/blob/main/src/tools/registry.ts)、[Skill 注册](https://github.com/virattt/dexter/blob/main/src/skills/registry.ts) 和 [scratchpad](https://github.com/virattt/dexter/blob/main/src/agent/scratchpad.ts)。继续参考工具记录与有限循环的分离；Turn 权威清单、一次重复纠正反馈、受限 Lambda、引用与预算恢复由本项目 Python 实现，不复制其工具权限或宣称同等模型效果。实际改编见 [本项目实现](../src/wonder_stockresearch/interactive/agent_working_record.py)。

## 公司研究框架

本版研究问题、门闸与维度依据用户既有 eco-moat-ai Standard 框架的概念整理。交接阶段核对了旧项目中该 skill 的公司问题、门闸和 M1–M6；本项目的中文研究规格是重新组织的需求说明，不复制旧版可执行 skill、不携带股东信、全文案例或代码。

旧 skill 内的“Source repo”只指向其本地目录，未给出可确认的上游仓库；在该 skill 目录未找到 LICENSE 文件。故本项目不声称已获得某个开源许可证，也不替原材料添加 MIT 等许可。若以后要复制/分发其原始文件，应先核对真正来源和适用许可。当前交接包已包含实施所需的公司问题与规则，不需访问旧目录。

研究规格保留 Q01–Q24、G1/G2/G3/G5/G6/G7、M1–M6 及 0–60 加总，并按用户要求把估值 Q25–Q28/G4 分离。新设的 insufficient/null、评分锚点与不自动生成投资动作均在规格中明示为本版行为。

## 架构依据（2026-09-10 核对）

- SQLite 适用于本地应用与低并发存储：[官方说明](https://www.sqlite.org/whentouse.html)。
- OpenCode 支持工具与权限配置：[工具文档](https://opencode.ai/docs/tools/)。先通过 CLI 使用 Python，后续必要时才接 MCP。
- Pi Coding Agent 支持 skills、会话与扩展，推荐 CLI 或扩展接入外部能力：[官方仓库](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md)。本版未对 OpenCode/Pi 做实际同条件性能对比，不宣称哪一个更省 token 或更准确。
- 富途 SDK：[官方仓库](https://github.com/FutunnOpen/py-futu-api)。2026-09-20 实测锁定 `futu-api==10.11.7108`：`OpenQuoteContext.get_market_snapshot` 与 `get_financials_statements`（statement_type 1–4，financial_type=ANNUAL）在本机 OpenD 返回真实数据；仅使用行情上下文，不调用账户/交易接口。
- SEC：[开发者入口](https://www.sec.gov/about/developer-resources)。2026-09-20 实测 `https://www.sec.gov/files/company_tickers.json` 在缺少带联系方式 User-Agent 时返回 403，故 SEC 通道需 `WONDER_SEC_USER_AGENT`，本环境未配置、标 blocked。
- HKEX：[披露入口](https://www.hkexnews.hk/)。2026-09-20 实测官方检索 `https://www1.hkexnews.hk/search/prefix.do`（查 stockId）与 `titleSearchServlet.do`（公告列表）返回真实数据。公司 IR 使用各公司正式网站。

这些外部链接是出处与实施核对入口，不是本地规格缺失内容的替代品。

## Pi Core 进一步核对（2026-10-08）

按用户要求核对 [Pi Agent Core](https://github.com/earendil-works/pi/tree/main/packages/agent)、[agent-loop.ts](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent-loop.ts) 与 [types.ts](https://github.com/earendil-works/pi/blob/main/packages/agent/src/types.ts)：借鉴调用边界的 transformContext/convertToLlm、ToolResult 模型内容/完整细节分离和可恢复工具错误。本项目自行处理累计预算、证据、只读补取与受限进程，不把这些能力归因于 Pi。查询与计算实现见[本项目源码](../src/wonder_stockresearch/interactive/query_workspace.py)。富途补取复用官方 Python SDK 的 OpenQuoteContext，本次在临时库验证真实财务补取。

## 交互 Agent 参考

用户指定的 Dexter 项目用于阶段 8–10 的交互 Agent 设计参考：

- [Dexter GitHub](https://github.com/virattt/dexter)
- [Agent loop](https://github.com/virattt/dexter/blob/main/src/agent/agent.ts)
- [Tool registry](https://github.com/virattt/dexter/blob/main/src/tools/registry.ts)
- [Skills registry](https://github.com/virattt/dexter/blob/main/src/skills/registry.ts)
- [Scratchpad](https://github.com/virattt/dexter/blob/main/src/agent/scratchpad.ts)

本项目只借鉴有限循环、工具注册、Skill 元数据、预算和调用轨迹，不复制 Dexter 的 Bun/TypeScript 技术栈，也不复制其开放网络、筛选、估值或交易能力。正式报告、InteractiveReport 和 Memo 的边界由本项目规格定义。

2026-10-06 又核对 [Pi Agent Core README](https://github.com/badlogic/pi-mono/tree/main/packages/agent)：其把 stateful loop、Tool 执行、事件流以及 `transformContext` 分开。本项目只借鉴“上下文变换独立于主循环”和“Tool schema 与执行分离”的结构，落实为 Python 的 `agent_memory.py`、`agent_policy.py`、`agent_tools.py` 与 `tool_registry.py`；未引入其 TypeScript runtime、streaming UI、并行 Tool batch、steering queue、扩展系统或多模型路由，也不据此宣称性能收益。

## 公司查询与上下文接入（2026-10-08）

本轮重新阅读 [Dexter loop](https://github.com/virattt/dexter/blob/main/src/agent/agent.ts)、[tool registry](https://github.com/virattt/dexter/blob/main/src/tools/registry.ts)、[skills registry](https://github.com/virattt/dexter/blob/main/src/skills/registry.ts) 和 [scratchpad](https://github.com/virattt/dexter/blob/main/src/agent/scratchpad.ts)，以及 [Pi Agent Core](https://github.com/badlogic/pi-mono/tree/main/packages/agent) 与 agent-loop 实现。借鉴受限工具/Skill 元数据、循环/记录与模型上下文转换分离；继续用 Python 原生模块。按 Turn 固定 Source/解析器版本、共享事实投影、输入空间分配及双库/计算文件备份为本项目实现，不能归因于上游框架。改编与实际验证见 [公司数据层 B](../src/wonder_stockresearch/interactive/agent_company.py)。


## Pi SDK 实际依赖（2026-10-09）

用户提供的独立 Pi 源码样例用于核对 0.83.0 的源码、类型与实际 Agent 生命周期。本项目在 `src/wonder_stockresearch/pi_runtime` 使用独立 package.json/package-lock.json 安装 MIT 许可的 `@earendil-works/pi-ai` 与 `@earendil-works/pi-agent-core` 0.83.0；没有依赖该参考目录或全局 Pi CLI。上游为 [earendil-works/pi](https://github.com/earendil-works/pi)，代码以锁定 npm 发行包为准，不以可能变化的 main 分支 API 代替当前类型。

实际复用原生 provider、Agent 循环、工具调度和事件。项目自行实现 Python 权限、来源/计算、预算持久化和事务适配；没有复用 pi-coding-agent 的 shell/文件工具、会话 UI 或扩展系统。继续核对用户指定的 [Dexter Agent](https://github.com/virattt/dexter/blob/main/src/agent/agent.ts)、[工具](https://github.com/virattt/dexter/blob/main/src/tools/registry.ts)、[Skill](https://github.com/virattt/dexter/blob/main/src/skills/registry.ts) 与 [Scratchpad](https://github.com/virattt/dexter/blob/main/src/agent/scratchpad.ts)，仅参考其受限调度和审计思路。本次 SDK 接入不代表真实研究质量已经通过，见 [Pi 运行时](technical-spec.md#1-模块与依赖)。

2026-10-10 经用户分别确认：Python 按 fixed/interactive/interactive.pi 分包，共用计算独立；仅项目 Pi 包装源码迁至 `pi_runtime/src/runner.ts`，SDK 的 Agent/消息/工具/流类型参与 strict 检查，构建后的 runner.mjs 随包发布。开发依赖 TypeScript 7.0.2、Node 类型 22.20.5 已锁定；不是将领域代码改写成上游实现，也未引入 coding-agent/Harness。实际 beforeToolCall/afterToolCall/subscribe/transformContext/followUp 复用及边界见 [技术规格](technical-spec.md#1-模块与依赖)。

同日可靠性修复再次核对上述Dexter主循环、工具/Skill注册和Scratchpad，参考其工具结果有界化与工作记录思路。当前Turn分页续查、紧凑引用回执、无工具的定向修复和预算快照均为本项目实现；没有复制上游开放权限或引入新模型审查Agent。现行可靠性契约见[技术规格](technical-spec.md#10-独立交互agent数值回答与公司memo)。

## AI开发流程方法（2026-10-12）

参考GitHub Spec Kit的需求到验收流程、OpenSpec的现行规格/变更/历史分离、Kiro按复杂度组织需求/设计/任务、OpenAI简洁AGENTS与验证约定，以及Anthropic的Agent结果评测。一手参考包括 [Spec Kit](https://github.github.com/spec-kit/)、[OpenSpec](https://github.com/Fission-AI/OpenSpec/blob/main/docs/overview.md)、[Kiro Specs](https://kiro.dev/docs/specs/)、[OpenAI AGENTS 约定](https://learn.chatgpt.com/docs/agent-configuration/agents-md)和 [Anthropic Agent 评测](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)。未安装这些工具或复制其实现，未新增付费judge或外部权限。当前产品技术栈以technical-spec为准；本文其他日期条目保留当时的来源核对记录。
