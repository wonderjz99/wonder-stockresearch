> 历史快照：截至2026-10-12整理前，仅供追溯；现行规则请读仓库根入口。正文保留，相对Markdown链接已重定位。

# wonder-stockresearch

2026-10-11 澄清与数值回答：ask/chat 共用 `question-clarification` 小 Skill；“近几年”先列年份确认，补充继续原 Turn。数值绑定成功 SQL 的事实身份，由 Python 渲染年份、原值、均值/极差/总体标准差和变化；未知单位不补百分号，已知季度缺口避免重复空查。使用与离线验证见 [本阶段教学](../../40-question-clarification-and-numeric-answers.md)，真实五题质量尚未复测。

2026-10-11 公司主备忘录：临时分析完成后在 `wonder>` 输入 `save`，原分析和引用追加到该公司唯一的备忘录，重复保存不会重复添加。腾讯文件是 `data/stocks/HK.00700/memo.md`；可用 `memo-read HK.00700` 查看、`--version` 读历史。旧 chat 可带原 `--session-id` 恢复后输入 save。用法与迁移说明见 [公司备忘录与 save](../../38-company-memo-save.md)。

2026-10-11 字段检索与错误恢复：位置未知先跨数据集找字段，两种运行时共享由执行记录生成的 Turn 工作清单；支持受限排序 Lambda，重复失败可获得一次纠正反馈，额度内用已有事实和缺口收尾。chat 执行前保存 Agent 关联，失败后可重试原问题。离线实现与使用见 [字段检索、工作清单与恢复](../../37-agent-query-and-recovery.md)；真实模型效果尚未复测。

2026-10-10 执行架构整理：两种问答运行时共用 Python 预算、工具执行与最终提交；Pi 接入原生工具前后钩子，JSONL 有版本和严格契约，公共 Python 模型请求从 Writer 拆出。代码已分为 fixed / interactive / interactive.pi，共用财务计算独立；Pi 包装源码改为 TypeScript，运行时使用构建的 JS。默认仍是 legacy，Pi 预览不变。调用链、SDK 复用和离线验证见 [执行架构说明](../../35-agent-execution-architecture.md)。

2026-10-09 更新：用户已批准 Python 领域核心 + 项目自带 Pi SDK 问答运行时。Pi 路径已实现并在验收，默认切换须待质量门槛通过；安装、职责和已知限制见 [Pi 运行时](../../30-pi-agent-runtime.md)。本更新覆盖下文历史阶段中的“暂不引入 Pi runtime”限制。

同日可靠性修复：新开放问题默认累计 60,000 tokens／10 轮；Pi 支持单轮输入上限、精确字段/多期间分页、当前 Turn 工作记录和紧凑引用修复，thinking 继续关闭。旧 Turn 与显式 30,000／6 输入不变，真实质量仍待复测，默认仍使用旧运行时。实现、配置与离线验证见 [Pi 问答可靠性修复](../../32-pi-agent-reliability.md)。

一个从零实现的个人港股、美股公司研究助手，同时作为逐步学习 Python 研究应用的项目。

**当前状态：业务阶段 1–18、阶段 19A 可选观测和阶段 19B Skill 驱动的受控工具路由、19C QueryWorkspace 和 19D.1 最小运行时已实现。** 固定研究已具备阶段 16/16.1 的 EvidencePack、可信计算、分组 Writer、语义审查、页级检索和三家电信公司的草稿验收材料；阶段 17 提供按需 Skill 加载与 Capability Registry。阶段 18 在既有 Python Agent loop 上加入来源双层优先级、富途财务单一采集路径、Scratchpad/SQLite FTS 记忆，以及只读数据集上的受限临时计算。三家电信公司的正式报告仍保持 draft，等待用户验收后决定是否冻结。

阶段 7.2 已为 DeepSeek writer 增加 `gap` schema 约束和一次受控结构修复；修复失败不会保存未经校验的草稿或自动 `finish`。

新执行 `finish` 的报告会展示完整 Q01–Q24 问题、中文状态标签、带千位分隔且保留原精度的数值，以及可追溯的短引用索引。交互终端会显示冻结结果摘要和报告绝对路径；管道/重定向仍输出 JSON。历史完成报告保持原样。详见[报告可读性与定稿反馈](../../11-report-presentation.md)。

DeepSeek Writer 现会收到同一份完整 Q01–Q24 问题定义，明确避免把利润率/现金流错放为 Q03 答案、把 EPS/资产负债表指标错放为 Q04 技术替代答案。自然语言研究未通过证据校验时，终端会显示具体错误与 run_id，不会静默退出或冻结报告。详见[Writer 问题对齐与校验失败反馈](../../12-question-alignment-and-validation-feedback.md)。

固定研究的分组输出现统一使用 `AnswerSection` 对象契约；损坏 JSON、结构修复、缓存复用、语义审查和 Python 硬校验共享同一条定向修订路径。`chat` 失败后输入“重试”会复用原 run 和成功分组。护城河同时显示严格证据总分与可用暂定总分；后者只对 null 维度使用透明的保守先验，不改写单项证据判断。腾讯、中国移动和中国电信的原 run 均保持 draft。详见[固定研究流程可靠性修复](../../18-fixed-flow-reliability.md)和[护城河评分与资料补全](../../19-moat-scoring-and-evidence-recovery.md)。

交互会话支持同 Turn 补充与失败重试：`ask-respond`、`ask-retry`；用 `ask-status` 查看 Attempt 时间线，遗留 running 状态可用 `ask-interrupt` 结算。重试继承预算，成功报告保留历史。用法见[执行可靠性与恢复](../../23-agent-runtime-reliability.md)。首轮误拦截的历史修复见[输入预算修复](../../24-agent-context-budget-fix.md)。目前进一步拆分模型上下文与工具完整结果；缺指标时可补取富途财务数据，由模型选择 SQL/受限 Python 计算。新问题默认总预算 30,000，可设 `allow_network=false` 禁止补取；旧 Turn 预算不变。真实验证与限制见[通用财务问答](../../25-agent-context-and-financial-tools.md)。

## 在新 Codex 会话中继续

以本目录作为 Codex 项目根目录，发送：

> 请先阅读 AGENTS.md，再按 PLAN.md 和 PROGRESS.md 继续。先核对规格，本次只完成下一阶段，运行验证，写对应的中文 Markdown 讲解并更新进度。不要一次实现全部阶段。

新会话不需要原聊天记录、不需要旧项目目录。开工前依照 [AGENTS.md](../../../AGENTS.md) 的阅读顺序读取全部必要规格。

## 已确定的产品方向

- 输入单只股票代码，使用项目内 Python CLI、DeepSeek API 和富途/其他已配置数据源即可独立完成公司研究；不需要 OpenCode 或任何外部宿主。
- 手动研究股票清单，不读取账户、持仓或资金，不执行交易。
- 公司报告覆盖 moat Standard 的公司部分：Q01–Q24、六项公司门闸、六项护城河评分。
- 原 28 问中的 Q25–Q28 属于估值，独立保留需求，首版不实现；不称为完整原版 28 问报告。
- 自动获取资料为主，允许补链接、PDF 或数据；证据缺口明确显示。
- 保存研究历史，手动发起复评并对比新旧证据和判断。

## 最小架构

当前完整讲解见 [项目架构总览](../../33-current-architecture.md)：包含 Python/Node 分工、双库与计算快照、Skill 授权、证据回执、Session/Turn/Attempt，以及默认路径和 Pi 预览的区别。

```text
CLI / chat → Python 路由与业务入口
             ├─ 固定研究：证据包 → Writer/Review → validate → 用户 finish
             └─ 开放问答：默认 Python loop / 显式 Node-Pi 预览
                              ↓
                   Python 受控工具、证据与预算
                              ↓
             wonder.db + company.db + 原文/隔离计算快照
                              ↓
                  临时报告 → 用户 memo-save（可选）
```

固定研究流程、Skill Registry 和自然语言入口由项目内 Python 提供；Pi 预览使用项目自带 Node 进程，不需要 OpenCode 或其他外部宿主。`chat` 会把 Skill Catalog 摘要提供给 DeepSeek Router，再选择固定研究或交互 Agent；正文和 reference 按需在执行边界加载。`run_steps` 记录采集/写作执行明细；交互 Agent 使用独立 Session/Turn/Attempt、消息、报告和 Memo 数据表，`chat_sessions` 保存自然语言确认和恢复状态。

当前 Skill 支持按需加载方法正文与 references。开放问题 Agent 会根据所选 Skill 获得相应 Python 工具 schema，并在这些获准工具内灵活选择调用顺序；比如 `analysis-workspace` Skill 授权归档财务查询、可信计算和 Turn 级受限 SQL/函数分析。新 Skill 可组合已注册函数，不必改 Agent loop；新函数仍须先在 Python Tool Registry 注册。Fixed Research 继续使用确定性流程。设计和权限边界见[阶段 19B：Skill 驱动的受控工具路由](../../17-skill-driven-tool-routing.md)和[Turn 级 QueryWorkspace](../../21-query-workspace.md)。

固定 Writer 和默认交互循环由 Python 直接调用 DeepSeek；显式 Pi 路径由 Node SDK 调用模型，Python 仍管理授权、证据、计算、累计预算与持久化。交互 Agent 只生成临时报告，用户明确 `memo-save` 或 chat `save` 才保存到公司主 Memo；不自动修改正式报告或执行 `finish`。LangGraph 仍需真实复杂度证据后再评估。

DeepSeek 调用的安全元数据会本地记录为标准 trace：用 `trace-read RUN_ID` 查看 Writer/Reviewer，
用 `agent-trace SESSION_ID` 查看 Agent 的模型与工具时间树。需要浏览器 UI 时可选导出到本机 Phoenix 或
Langfuse；显式设置 `WONDER_TRACE_CAPTURE_CONTENT=preview|full` 后，后端还能显示可见模型 I/O 与工具 I/O
（本地 SQLite 仍不保存正文）。完整配置、隐私边界和命令见[调用轨迹、Phoenix 与 Langfuse](../../16-tracing-and-phoenix.md)。

## 独立运行

自然语言和 Agent 能力由项目 Python CLI 提供，`Skill Registry` 会直接读取 `skills/**/SKILL.md`，不需要单独安装 skill 到外部宿主：

```bash
wonder-research --root <项目根> ask HK.00728 --input question.json
wonder-research --root <项目根> ask-followup <SESSION_ID> --input followup.json
wonder-research --root <项目根> memo-save <SESSION_ID> --input memo.json
wonder-research --root <项目根> memory-search HK.00941 --query "ROE"
wonder-research --root <项目根> memory-read <MEMORY_ID>
wonder-research --root <项目根> chat
wonder-research --root <项目根> chat --text "帮我研究一下中国移动"
```

`opencode.json` 和阶段 5 文档只保留历史集成记录，不属于项目运行依赖。未来用户自建 Agent 可以按需调用本项目 CLI/API，但固定研究、交互分析和数据归档在本项目 Python 运行时内完成。

`chat` 支持不严格的自然语言。系统会先识别意图和公司，并在启动正式研究前询问确认；正式研究自动执行到 `validate`，通过后还要再次确认才会 `finish`。如果简称有歧义，例如“保利”，系统会先查本地清单和富途 `get_search_quote`，展示候选并等待用户输入编号、完整名称或 ticker，不会自动猜测。直接在终端使用 `chat --text` 时，会保留当前 session 并继续等待确认/追问；如果 stdout 被管道或重定向，则保持一次性 JSON 输出，适合脚本。长任务的简短进度写入 stderr。

## 配置 DeepSeek（可选，项目根 .env）

`write-draft` 需要 DeepSeek 凭据。项目会加载**项目根目录的 `.env`**（已加入 `.gitignore`，不会提交）：

```bash
cp .env.example .env      # 已为你创建；打开填入自己的值
# .env 内容示例：
# DEEPSEEK_API_KEY=sk-你的key
# WONDER_DEEPSEEK_MODEL=deepseek-flash
```

- `.env` 与 `.env.example` 是不同的文件：`.env` 本地保存密钥、永不提交；`.env.example` 是模板，会提交。
- 已存在的 shell 环境变量优先，`.env` 不覆盖它们；密钥不写入数据库、日志或 `run_steps`。
- 加载发生在 `--root` 解析之后；若你把 `.env` 放在项目根，项目内 CLI 会读取，无需额外 export。
- 若对已完成 run 调用 `write-draft`，会得到 `run_completed`；请先 `start` 新 run 并 `collect`/`source-add`。

`write-draft` 默认 **关闭 thinking**（`WONDER_DEEPSEEK_THINKING=disabled`）并使用 **温度 0.2**（`WONDER_DEEPSEEK_TEMPERATURE`），可通过 `--thinking`、`--temperature` 覆盖。DeepSeek 官方文档说明 thinking 模式默认开启且不支持 temperature；结构化写作关闭 thinking 可避免推理 token 挤占输出预算（否则 JSON 会被截断）。文档与命令示例见 [阶段 7](../../07-deepseek-writer.md)。

## 给后续 coding agent 的固定交代

每次继续开发时，先读 [AGENTS.md](../../../AGENTS.md)、[PLAN.md](../../../PLAN.md)、[PROGRESS.md](../../../PROGRESS.md)、[docs/09-agent-layer.md](../../09-agent-layer.md) 及技术规格，再检查代码、工作区改动和实际测试。实现交互 Agent 时必须参考用户指定的 [Dexter](https://github.com/virattt/dexter) 主循环、工具注册表、Skills 和 Scratchpad，但保持 Python/CLI/SQLite 核心；模型输出必须经过 Pydantic 和来源边界校验。不要引入 LangGraph，除非 PLAN 规定的复杂度门槛已满足。

## 阅读入口

- [当前项目架构](../../33-current-architecture.md)：从整体结构到一次输入的完整调用链，说明当前默认实现、Pi 预览、数据与权限边界。
- [执行架构与 Pi 钩子](../../35-agent-execution-architecture.md)：共用预算/工具/提交操作、版本协议、直接 handler 与公共模型请求。
- [字段检索、工作清单与恢复](../../37-agent-query-and-recovery.md)：跨数据集定位、Turn 清单、受限排序与重复失败后的有限继续。
- [公司备忘录与 save](../../38-company-memo-save.md)：每公司唯一主备忘录、分析后快捷保存、历史版本与旧记录兼容。
- [公司数据现状与 30 道 Agent 测试题](../../26-company-data-and-agent-eval.md)：本地覆盖、可直接使用的输入文件、验收要点和后续数据层计划；题集尚未执行。
- [独立公司数据库计划](../../27-company-database-plan.md)：company.db 与 wonder.db 的职责、来源版本、刷新、备份和分阶段验收；A、B 已完成，C–D 待实施。
- [公司数据层 A：离线解析与回填](../../28-company-data-store.md)：`company-data sync/status/fields/read/rebuild`；已回填七家公司四类归档，支持双库备份。
- [公司数据层 B：Agent 共享查询](../../29-company-agent-query.md)：新 chat 使用 company_latest，支持字段/期间目录、补取后索引、来源版本固定、本 Turn 引用和计算产物备份；包含真实冒烟的口径限制。

- [实施计划](../../../PLAN.md)：阶段目标与验收。
- [进度](../../../PROGRESS.md)：实际完成情况和下一步。
- [阶段 2：SQLite 与股票历史](../../02-storage-and-history.md)：表结构、run 生命周期与写锁。
- [阶段 3：来源存档与证据定位](../../03-sources-and-evidence.md)：下载/导入、哈希、提取与片段读取。
- [阶段 4：真实资料与富途只读接入](../../04-data-connectors.md)：collect、run_steps、HKEX/SEC 与富途适配器。
- [阶段 5 历史集成记录](../../05-opencode-and-tools.md)：早期 OpenCode + CLI 实跑；非当前依赖或产品运行路径。
- [阶段 6：手动复评、备份与维护](../../06-review-and-maintenance.md)：compare、review.md、backup 与恢复验证。
- [阶段 7：DeepSeek 直接写作入口](../../07-deepseek-writer.md)：write-draft、受控上下文、凭据与 run_steps 记录。
- [富途数据能力清单](../../08-futu-data-capabilities.md)：futu-api 能提供哪些数据、只读边界与 as_of 可用性。
- [交互 Agent 层](../../09-agent-layer.md)：固定流程、临时报告、Memo、独立运行和 Dexter 参考边界。
- [阶段 19B：Skill 驱动的受控工具路由](../../17-skill-driven-tool-routing.md)：Skill manifest、Python Tool Registry、开放 Agent 的动态工具授权与执行边界。
- [固定研究流程可靠性修复](../../18-fixed-flow-reliability.md)：统一 Writer 契约、同 run 恢复、硬校验修订和三家公司真实验收。
- [护城河评分与资料补全](../../19-moat-scoring-and-evidence-recovery.md)：官方/Futu 已归档资料的补全范围、维度定义与严格/暂定双轨总分。
- [独立自然语言入口](../../10-standalone-natural-language.md)：Skill Registry、Router、chat、确认和进度事件。
- [调用轨迹、Phoenix 与 Langfuse](../../16-tracing-and-phoenix.md)：`trace-read`、`agent-trace`、可选 OTLP 导出、Agent Graph 与隐私边界。
- [报告可读性与定稿反馈](../../11-report-presentation.md)：终端完成摘要、正式报告排版和短引用索引。
- [Writer 问题对齐与校验失败反馈](../../12-question-alignment-and-validation-feedback.md)：完整问题上下文、Q03/Q04 专项约束和失败诊断。
- [固定研究质量流水线](../../13-fixed-research-quality.md)：分组证据包、可信计算、语义审查、断点恢复和三家电信实测。
- [Skill 按需加载与 Capability Registry](../../14-skill-on-demand.md)：Catalog、方法/能力 Skill、执行审计和 CLI。
- [开放问题 Agent、记忆与临时计算](../../15-open-question-agent.md)：来源仲裁、Scratchpad、长期记忆、AnalysisWorkspace 与阶段 19C–22 路线。
- [交互 Agent 的内部组织](../../20-agent-runtime-organization.md)：system prompt、Tool handler、Scratchpad 与有限循环的代码边界。
- [Turn 级 QueryWorkspace](../../21-query-workspace.md)：隔离财务快照、只读 SQL、受限函数、证据回执与 19C 边界。
- [研究规格](../../research-spec.md)：问题、门闸、评分和证据规则。
- [技术规格](../../technical-spec.md)：数据与 CLI 契约。
- [教学约定](../../teaching-contract.md)：每阶段如何实现、验证和讲解。
- [框架出处与设计依据](../../provenance.md)：必要来源及改编边界。

## 目标技术栈

Python 3.11、uv、Pydantic 2、标准库 sqlite3/argparse、pytest。按阶段再加入 HTTP 客户端、PDF 文本提取与富途 SDK。

## 许可证

本项目采用 [MIT License](../../../LICENSE)。

已实际验证的命令：

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run wonder-research --help
uv run wonder-research --root <项目根> start HK.00728
uv run wonder-research --root <项目根> collect <RUN_ID> --kind filings
uv run wonder-research --root <项目根> collect <RUN_ID> --kind market
uv run wonder-research --root <项目根> collect <RUN_ID> --kind fundamentals
uv run wonder-research --root <项目根> start HK.00728 --baseline <BASELINE_RUN>
uv run wonder-research --root <项目根> skill-list
uv run wonder-research --root <项目根> skill-check company-research
uv run wonder-research --root <项目根> start HK.00728 --skill company-research
uv run wonder-research --root <项目根> compare <RUN_ID>
uv run wonder-research --root <项目根> backup --output data/backups/<NAME>
DEEPSEEK_API_KEY=... uv run wonder-research --root <项目根> write-draft <RUN_ID> --strategy staged
DEEPSEEK_API_KEY=... uv run wonder-research --root <项目根> review-draft <RUN_ID>
uv run wonder-research --root <项目根> draft-read <RUN_ID>
uv run wonder-research --root <项目根> review-read <RUN_ID>
uv run wonder-research --root <项目根> draft-preview <RUN_ID>
DEEPSEEK_API_KEY=... uv run wonder-research --root <项目根> ask HK.00728 --input question.json
DEEPSEEK_API_KEY=... uv run wonder-research --root <项目根> chat --text "分析中国电信未来两年的主要风险"
```

`write-draft` 只在运行环境读取 `DEEPSEEK_API_KEY`（不写入项目/日志/数据库），默认模型 `deepseek-flash`，可用 `--model` 或 `WONDER_DEEPSEEK_MODEL` 覆盖；默认分六组写作并做语义审查，只保存草稿，不会自动 `finish`。schema v6 及以后的新 standard run 必须有当前草稿对应的 passing review 才能定稿；schema v7 另为已归档年报建立本地页级检索索引。

如既有模型审查出现数值计数、精度或四舍五入误报，可执行 `uv run wonder-research --root . review-draft <RUN_ID> --recheck`。它只用归档来源离线复核当前草稿的同 hash 模型审查，追加带核对说明的审查记录；不调用 API、不改草稿、不自动定稿。无法证明的 error 仍保留，过期审查和人工审查不复用。通过语义审查也不等于资料齐全或判断已证实。

`collect --kind market` 需要本机 OpenD（默认 `127.0.0.1:11111`，可用 `WONDER_FUTU_HOST`/`WONDER_FUTU_PORT` 覆盖），只归档行情快照；财务报表仅由 `collect --kind fundamentals` 的 `futu_fundamentals.statements` 获取。`collect --kind filings` 使用 HKEX 官方渠道，SEC 通道需设置 `WONDER_SEC_USER_AGENT`。这些都是用户运行环境配置，程序不读取账户秘密。

各阶段说明见 [阶段 1：离线公司研究闭环](../../01-minimal-research.md)、[阶段 2：SQLite 与股票历史](../../02-storage-and-history.md)、[阶段 3：来源存档与证据定位](../../03-sources-and-evidence.md)、[阶段 4：真实资料与富途只读接入](../../04-data-connectors.md)、[阶段 5 历史集成记录](../../05-opencode-and-tools.md)、[阶段 6：手动复评、备份与维护](../../06-review-and-maintenance.md)、[阶段 7：DeepSeek 直接写作入口](../../07-deepseek-writer.md)、[富途数据能力清单](../../08-futu-data-capabilities.md)、[Skill 按需加载](../../14-skill-on-demand.md)。正式报告由 `finish` 从已校验草稿生成并归档；有 baseline 时同时生成 `review.md`；`write-draft` 只生成草稿；`render-packet` 仍只是离线样例，不是正式完成记录。
