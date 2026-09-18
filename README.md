# wonder-stockresearch

一个从零实现的个人港股、美股公司研究助手，同时作为逐步学习 Python 研究应用的项目。

**当前状态：已完成阶段 2 的 SQLite 与股票历史。** 可运行阶段 1 的 `validate-packet`/`render-packet`，以及阶段 2 的 `watch`、`start`、`save-draft`、`validate`、`finish`、`resume`、`status`、`history`；研究记录持久化在 `data/`，完成后不可覆盖。真实数据接口、OpenCode skill、附记与估值仍未实现。

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

先做独立 Python CLI，再由 OpenCode 使用。Pi Coding Agent 可以在以后试用同一 CLI，首版不同时适配两个宿主。首版不自研 Agent、不接模型 SDK、不先做 MCP。后续可由 Python 直接调用 DeepSeek 等模型 API 生成结构化研究草稿，复用同一套校验、评分与报告服务；这是后续扩展，不是当前已实现能力。

## 阅读入口

- [实施计划](PLAN.md)：阶段目标与验收。
- [进度](PROGRESS.md)：实际完成情况和下一步。
- [阶段 2：SQLite 与股票历史](docs/02-storage-and-history.md)：表结构、run 生命周期与写锁。
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
```

离线包的字段和操作说明见 [阶段 1：离线公司研究闭环](docs/01-minimal-research.md)；持久化研究记录见 [阶段 2：SQLite 与股票历史](docs/02-storage-and-history.md)。阶段 2 的正式报告由 `finish` 从已校验草稿生成并归档；`render-packet` 仍只是离线样例，不是正式完成记录。
