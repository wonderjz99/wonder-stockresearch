# wonder-stockresearch

一个从零实现的个人港股、美股公司研究助手，同时作为逐步学习 Python 研究应用的项目。

**当前状态：已完成阶段 5 的 OpenCode + CLI 端到端研究。** 可运行阶段 1 的 `validate-packet`/`render-packet`，阶段 2 的 `watch`、`start`、`save-draft`、`validate`、`finish`、`resume`、`status`、`history`，阶段 3 的 `source-add`、`sources`、`source-read`，阶段 4 的 `collect`（富途只读行情/财务 + HKEX/SEC 公告发现，带 `run_steps` 账本），以及阶段 5 的 project skill `skills/company-research/`（由 `opencode.json` 的 `skills.paths` 注册）。已用它完成一次 HK.00728 人工监督端到端研究。附记、估值与复评 compare 仍未实现。

## 在新 Codex 会话中继续

以本目录作为 Codex 项目根目录，发送：

> 请先阅读 AGENTS.md，再按 PLAN.md 和 PROGRESS.md 继续。先核对规格，本次只完成下一阶段，运行验证，写对应的中文 Markdown 讲解并更新进度。不要一次实现全部阶段。

新会话不需要原聊天记录、不需要旧项目目录。开工前依照 [AGENTS.md](AGENTS.md) 的阅读顺序读取全部必要规格。

## 已确定的产品方向

- 输入单只股票代码，借助 OpenCode 和 Python 完成公司研究。
- 手动研究股票清单，不读取账户、持仓或资金，不执行交易。
- 公司报告覆盖 moat Standard 的公司部分：Q01–Q24、六项公司门闸、六项护城河评分。
- 原 28 问中的 Q25–Q28 属于估值，独立保留需求，首版不实现；不称为完整原版 28 问报告。
- 自动获取资料为主，允许补链接、PDF 或数据；证据缺口明确显示。
- 保存研究历史，手动发起复评并对比新旧证据和判断。

## 最小架构

```text
用户 ↔ OpenCode + 公司研究 skill（第 5 阶段）
                     ↓ CLI / JSON
             Python 研究服务
              ↙           ↘
       富途与公开资料     SQLite + 股票文件夹
                              ↓
                        Markdown 报告
```

先做独立 Python CLI，再由 OpenCode 使用。当前不把核心改成 Agent；OpenCode 是受人工监督的宿主，SQLite/文件和 Python 服务才是研究事实来源。阶段 4 已增加轻量 `run_steps` 执行明细（schema v2），用于采集任务、失败重试和 provider 错误记录，但不引入通用工作流框架。

Pi Coding Agent 可以在以后试用同一 CLI，首版不同时适配两个宿主。后续可由 Python 直接调用 DeepSeek 等模型 API 生成结构化研究草稿，复用同一套校验、评分与报告服务；只有真实端到端研究出现动态补证据、跨天恢复或多步并行等需求时，才评估独立的 LangGraph 编排层。Temporal、Prefect、多 Agent 和 MCP 均不属于当前首版。

## 在 OpenCode 中使用

项目 skill 正文在 `skills/company-research/SKILL.md`，由项目根 `opencode.json` 注册：

```json
{ "$schema": "https://opencode.ai/config.json", "skills": { "paths": ["skills"] } }
```

在该目录启动 OpenCode，重启一次以加载 skill，然后用自然语言让宿主按 skill 调用 `uv run wonder-research ...`。skill 只调用已有 CLI（start/collect/sources/source-read/source-add/save-draft/validate/finish/status/history），不接 MCP、不读账户、不让模型直接写数据库。修改 skill 或 `opencode.json` 后需再次重启。

## 给后续 coding agent 的固定交代

每次继续开发时，先读 [AGENTS.md](AGENTS.md)、[PLAN.md](PLAN.md)、[PROGRESS.md](PROGRESS.md) 及技术规格，再检查代码、工作区改动和实际测试。默认只实现 `PROGRESS.md` 指定的下一个阶段：保持函数/CLI/SQLite/文件核心，不提前引入 LangGraph、Temporal、Prefect、MCP 或多 Agent；模型输出必须经过现有 Pydantic、引用、评分和 `finish` 校验；完成代码、测试、中文讲解和进度更新后暂停。

## 阅读入口

- [实施计划](PLAN.md)：阶段目标与验收。
- [进度](PROGRESS.md)：实际完成情况和下一步。
- [阶段 2：SQLite 与股票历史](docs/02-storage-and-history.md)：表结构、run 生命周期与写锁。
- [阶段 3：来源存档与证据定位](docs/03-sources-and-evidence.md)：下载/导入、哈希、提取与片段读取。
- [阶段 4：真实资料与富途只读接入](docs/04-data-connectors.md)：collect、run_steps、HKEX/SEC 与富途适配器。
- [阶段 5：OpenCode + CLI 端到端研究](docs/05-opencode-and-tools.md)：project skill、宿主职责与 HK.00728 实跑记录。
- [研究规格](docs/research-spec.md)：问题、门闸、评分和证据规则。
- [技术规格](docs/technical-spec.md)：数据与 CLI 契约。
- [教学约定](docs/teaching-contract.md)：每阶段如何实现、验证和讲解。
- [框架出处与设计依据](docs/provenance.md)：必要来源及改编边界。

## 目标技术栈

Python 3.11、uv、Pydantic 2、标准库 sqlite3/argparse、pytest。按阶段再加入 HTTP 客户端、PDF 文本提取与富途 SDK。

已实际验证的命令：

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run wonder-research --help
uv run wonder-research --root <项目根> start HK.00728
uv run wonder-research --root <项目根> collect <RUN_ID> --kind filings
uv run wonder-research --root <项目根> collect <RUN_ID> --kind market
```

`collect --kind market` 需要本机 OpenD（默认 `127.0.0.1:11111`，可用 `WONDER_FUTU_HOST`/`WONDER_FUTU_PORT` 覆盖），只读行情/财务；`collect --kind filings` 使用 HKEX 官方渠道，SEC 通道需设置 `WONDER_SEC_USER_AGENT`。这些都是用户运行环境配置，程序不读取账户秘密。

各阶段说明见 [阶段 1：离线公司研究闭环](docs/01-minimal-research.md)、[阶段 2：SQLite 与股票历史](docs/02-storage-and-history.md)、[阶段 3：来源存档与证据定位](docs/03-sources-and-evidence.md)、[阶段 4：真实资料与富途只读接入](docs/04-data-connectors.md)、[阶段 5：OpenCode + CLI 端到端研究](docs/05-opencode-and-tools.md)。正式报告由 `finish` 从已校验草稿生成并归档；`render-packet` 仍只是离线样例，不是正式完成记录。
