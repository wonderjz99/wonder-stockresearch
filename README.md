# wonder-stockresearch

一个从零实现的个人港股、美股公司研究助手，同时作为逐步学习 Python 研究应用的项目。

**当前状态：阶段 1–18 已实现。** 固定研究已具备阶段 16/16.1 的 EvidencePack、可信计算、分组 Writer、语义审查、页级检索和三家电信公司的草稿验收材料；阶段 17 提供按需 Skill 加载与 Capability Registry。阶段 18 在既有 Python Agent loop 上加入来源双层优先级、富途财务单一采集路径、Scratchpad/SQLite FTS 记忆，以及只读数据集上的受限临时计算。三家电信公司的正式报告仍保持 draft，等待用户验收后决定是否冻结。

阶段 7.2 已为 DeepSeek writer 增加 `gap` schema 约束和一次受控结构修复；修复失败不会保存未经校验的草稿或自动 `finish`。

新执行 `finish` 的报告会展示完整 Q01–Q24 问题、中文状态标签、带千位分隔且保留原精度的数值，以及可追溯的短引用索引。交互终端会显示冻结结果摘要和报告绝对路径；管道/重定向仍输出 JSON。历史完成报告保持原样。详见[报告可读性与定稿反馈](docs/11-report-presentation.md)。

DeepSeek Writer 现会收到同一份完整 Q01–Q24 问题定义，明确避免把利润率/现金流错放为 Q03 答案、把 EPS/资产负债表指标错放为 Q04 技术替代答案。自然语言研究未通过证据校验时，终端会显示具体错误与 run_id，不会静默退出或冻结报告。详见[Writer 问题对齐与校验失败反馈](docs/12-question-alignment-and-validation-feedback.md)。

## 在新 Codex 会话中继续

以本目录作为 Codex 项目根目录，发送：

> 请先阅读 AGENTS.md，再按 PLAN.md 和 PROGRESS.md 继续。先核对规格，本次只完成下一阶段，运行验证，写对应的中文 Markdown 讲解并更新进度。不要一次实现全部阶段。

新会话不需要原聊天记录、不需要旧项目目录。开工前依照 [AGENTS.md](AGENTS.md) 的阅读顺序读取全部必要规格。

## 已确定的产品方向

- 输入单只股票代码，使用项目内 Python CLI、DeepSeek API 和富途/其他已配置数据源即可独立完成公司研究；不需要 OpenCode 或任何外部宿主。
- 手动研究股票清单，不读取账户、持仓或资金，不执行交易。
- 公司报告覆盖 moat Standard 的公司部分：Q01–Q24、六项公司门闸、六项护城河评分。
- 原 28 问中的 Q25–Q28 属于估值，独立保留需求，首版不实现；不称为完整原版 28 问报告。
- 自动获取资料为主，允许补链接、PDF 或数据；证据缺口明确显示。
- 保存研究历史，手动发起复评并对比新旧证据和判断。

## 最小架构

```text
固定 Prompt/CLI ───────────────→ 标准 ResearchRun → 正式报告
开放问题 → 独立交互 Agent → 临时 InteractiveReport → 用户确认后 Memo
                         ↓
                 Scratchpad + 受限 AnalysisWorkspace
                         ↓
              Python 研究服务 + DeepSeek API
                 ↙                    ↘
          富途/归档资料          SQLite + 股票文件夹
```

固定研究流程、交互 Agent、Skill Registry 和自然语言入口都由项目内 Python 独立提供，不需要 OpenCode 或其他外部宿主。`chat` 会把 Skill Catalog 摘要提供给 DeepSeek Router，再选择固定研究或交互 Agent；正文和 reference 只有在确认后的执行边界才加载。阶段 4 的 `run_steps` 仍只是采集执行账本；交互 Agent 使用独立 session/event/report/Memo 数据表，`chat_sessions` 只保存自然语言确认和恢复状态，不修改正式 ResearchRun。

阶段 7 由 Python 直接调用 DeepSeek 生成正式报告草稿；阶段 8–10 的交互 Agent 同样由 Python 直接调用 DeepSeek，用户只需配置 API Key 以及富途/OpenD 或已归档资料即可运行。交互 Agent 只生成临时报告，只有 `memo-save` 才保存 Memo；不自动修改正式报告或执行 `finish`。LangGraph 仍需真实复杂度证据后再评估。

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

`write-draft` 默认 **关闭 thinking**（`WONDER_DEEPSEEK_THINKING=disabled`）并使用 **温度 0.2**（`WONDER_DEEPSEEK_TEMPERATURE`），可通过 `--thinking`、`--temperature` 覆盖。DeepSeek 官方文档说明 thinking 模式默认开启且不支持 temperature；结构化写作关闭 thinking 可避免推理 token 挤占输出预算（否则 JSON 会被截断）。文档与命令示例见 [阶段 7](docs/07-deepseek-writer.md)。

## 给后续 coding agent 的固定交代

每次继续开发时，先读 [AGENTS.md](AGENTS.md)、[PLAN.md](PLAN.md)、[PROGRESS.md](PROGRESS.md)、[docs/09-agent-layer.md](docs/09-agent-layer.md) 及技术规格，再检查代码、工作区改动和实际测试。实现交互 Agent 时必须参考用户指定的 [Dexter](https://github.com/virattt/dexter) 主循环、工具注册表、Skills 和 Scratchpad，但保持 Python/CLI/SQLite 核心；模型输出必须经过 Pydantic 和来源边界校验。不要引入 LangGraph，除非 PLAN 规定的复杂度门槛已满足。

## 阅读入口

- [实施计划](PLAN.md)：阶段目标与验收。
- [进度](PROGRESS.md)：实际完成情况和下一步。
- [阶段 2：SQLite 与股票历史](docs/02-storage-and-history.md)：表结构、run 生命周期与写锁。
- [阶段 3：来源存档与证据定位](docs/03-sources-and-evidence.md)：下载/导入、哈希、提取与片段读取。
- [阶段 4：真实资料与富途只读接入](docs/04-data-connectors.md)：collect、run_steps、HKEX/SEC 与富途适配器。
- [阶段 5 历史集成记录](docs/05-opencode-and-tools.md)：早期 OpenCode + CLI 实跑；非当前依赖或产品运行路径。
- [阶段 6：手动复评、备份与维护](docs/06-review-and-maintenance.md)：compare、review.md、backup 与恢复验证。
- [阶段 7：DeepSeek 直接写作入口](docs/07-deepseek-writer.md)：write-draft、受控上下文、凭据与 run_steps 记录。
- [富途数据能力清单](docs/08-futu-data-capabilities.md)：futu-api 能提供哪些数据、只读边界与 as_of 可用性。
- [交互 Agent 层](docs/09-agent-layer.md)：固定流程、临时报告、Memo、独立运行和 Dexter 参考边界。
- [独立自然语言入口](docs/10-standalone-natural-language.md)：Skill Registry、Router、chat、确认和进度事件。
- [报告可读性与定稿反馈](docs/11-report-presentation.md)：终端完成摘要、正式报告排版和短引用索引。
- [Writer 问题对齐与校验失败反馈](docs/12-question-alignment-and-validation-feedback.md)：完整问题上下文、Q03/Q04 专项约束和失败诊断。
- [固定研究质量流水线](docs/13-fixed-research-quality.md)：分组证据包、可信计算、语义审查、断点恢复和三家电信实测。
- [Skill 按需加载与 Capability Registry](docs/14-skill-on-demand.md)：Catalog、方法/能力 Skill、执行审计和 CLI。
- [开放问题 Agent、记忆与临时计算](docs/15-open-question-agent.md)：来源仲裁、Scratchpad、长期记忆、AnalysisWorkspace 与阶段 19–21 路线。
- [研究规格](docs/research-spec.md)：问题、门闸、评分和证据规则。
- [技术规格](docs/technical-spec.md)：数据与 CLI 契约。
- [教学约定](docs/teaching-contract.md)：每阶段如何实现、验证和讲解。
- [框架出处与设计依据](docs/provenance.md)：必要来源及改编边界。

## 目标技术栈

Python 3.11、uv、Pydantic 2、标准库 sqlite3/argparse、pytest。按阶段再加入 HTTP 客户端、PDF 文本提取与富途 SDK。

## 许可证

本项目采用 [MIT License](LICENSE)。

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

`collect --kind market` 需要本机 OpenD（默认 `127.0.0.1:11111`，可用 `WONDER_FUTU_HOST`/`WONDER_FUTU_PORT` 覆盖），只归档行情快照；财务报表仅由 `collect --kind fundamentals` 的 `futu_fundamentals.statements` 获取。`collect --kind filings` 使用 HKEX 官方渠道，SEC 通道需设置 `WONDER_SEC_USER_AGENT`。这些都是用户运行环境配置，程序不读取账户秘密。

各阶段说明见 [阶段 1：离线公司研究闭环](docs/01-minimal-research.md)、[阶段 2：SQLite 与股票历史](docs/02-storage-and-history.md)、[阶段 3：来源存档与证据定位](docs/03-sources-and-evidence.md)、[阶段 4：真实资料与富途只读接入](docs/04-data-connectors.md)、[阶段 5 历史集成记录](docs/05-opencode-and-tools.md)、[阶段 6：手动复评、备份与维护](docs/06-review-and-maintenance.md)、[阶段 7：DeepSeek 直接写作入口](docs/07-deepseek-writer.md)、[富途数据能力清单](docs/08-futu-data-capabilities.md)、[Skill 按需加载](docs/14-skill-on-demand.md)。正式报告由 `finish` 从已校验草稿生成并归档；有 baseline 时同时生成 `review.md`；`write-draft` 只生成草稿；`render-packet` 仍只是离线样例，不是正式完成记录。
