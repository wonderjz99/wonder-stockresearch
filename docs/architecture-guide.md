# 项目架构导览：给有 Python 基础的读者

这份文档解释**当前代码怎样协作**。你可以把项目看成一个在本机运行的 Python 命令行程序：它收集公司资料，生成结构化研究草稿，检查证据，再把通过检查的草稿保存为正式报告。数据留在本项目的 `data/` 目录。

如果只记住一件事：**DeepSeek 负责提出文字判断，Python 负责决定这些判断能否进入正式报告。**

## 1. 先看全图

```text
你输入命令或一句话
        │
        ▼
cli.py ── 命令解析、JSON 输入输出
        │
        ├─ 固定研究：research.py ── 收集、草稿、校验、定稿、复评
        │                  │
        │                  ├─ sources.py / market.py / futu_fundamentals.py /
        │                  │  disclosures.py ── 外部资料的只读获取
        │                  ├─ writer.py ── 调用 DeepSeek 写结构化草稿
        │                  ├─ models.py ── Pydantic 数据形状
        │                  └─ reports.py ── 渲染正式报告
        │
        ├─ 自然语言：chat.py → router.py ── 理解意图、确认、选择上面的流程
        │
        └─ 开放问题：agent.py ── 有限工具循环、临时报告、显式保存 Memo
                           │
                           ▼
                  storage.py ── SQLite + data/ 文件
```

这不是 Web 服务：没有前后端服务器，也不要求 OpenCode 作为运行宿主。`wonder-research` 是在你的终端里运行的命令，入口在 [`cli.py`](../src/wonder_stockresearch/cli.py) 的 `main()`；`python -m wonder_stockresearch` 走同一个入口。项目用 Python 3.11、Pydantic、标准库 `sqlite3`/`argparse`，加上资料获取所需的库。

## 2. 几个对象先认清

| 名称 | 可以理解成 | 在哪里 |
| --- | --- | --- |
| `ticker` | 一家公司的股票代码，例如 `HK.00728` | 多张表和文件夹的归属键 |
| `run_id` / `ResearchRun` | **一次**正式研究的编号；同一公司可以研究多次 | SQLite `runs` 表 |
| `as_of` | 这次研究允许使用资料的截止时点 | `runs` 与 `ResearchDraft` |
| `Source` | 一次取得的资料及其标题、日期、哈希、原文路径等 | SQLite `sources` 表和 `data/` 原文文件 |
| `EvidenceRef` | 指向一个 `source_id` 的具体页码、行号或接口字段 | 草稿中的陈述和数字 |
| `ResearchDraft` | Q01–Q24、公司门闸、护城河评分、反证等结构化草稿 | SQLite `runs.draft_json` |
| `ResearchPacket` | `ResearchDraft` 加本次可引用的 `Source` 列表 | Python 函数之间传递的对象 |
| `InteractiveReport` | 针对开放问题的一次临时回答 | SQLite `interactive_reports` 表 |
| `CompanyMemo` | 用户明确要求长期保存的观察 | SQLite `company_memos` 表 |

`run_id` 很重要。它像一次实验的编号：同一只股票的旧研究、正在写的草稿、以后发起的复评，彼此不能混成一个版本。`source_id` 则像资料编号；只写“见年报”不足以定位证据，还要有页码等 `locator`。

这些对象的字段和允许值在 [`models.py`](../src/wonder_stockresearch/models.py)。Pydantic 先拒绝字段缺失、类型错误、额外字段等**形状错误**。例如评分只能是 0–10 或 `null`；`null` 表示没有足够资料评估，并不等于 0 分。

## 3. 固定公司研究：一次输入如何变成报告

这是生成正式 Q01–Q24 公司报告的主流程：

```text
start TICKER
  → 得到 run_id，状态 draft
collect RUN_ID / source-add RUN_ID
  → 资料归档为 Source，并关联到这次 run
source-read RUN_ID SOURCE_ID
  → 按页或行查看资料片段
write-draft RUN_ID（或手工 save-draft）
  → 得到 ResearchDraft，保存到 runs.draft_json
validate RUN_ID
  → 检查结构、引用、时间边界和原文归档
finish RUN_ID
  → 再校验一次，写出 company.md + research.json，状态 completed
```

对应的主要函数依次是 [`research.start_run()`](../src/wonder_stockresearch/fixed/research.py)、`research.collect()` / `research.source_add()`、[`writer.write_draft()`](../src/wonder_stockresearch/fixed/writer.py)、`research.validate_run()` 和 `research.finish_run()`。CLI 只解析参数、调用这些函数、输出结果；它不自己计算评分或绕过校验。

### 资料从哪里来？

`collect` 按需调用 [`market.py`](../src/wonder_stockresearch/market.py) 的富途只读行情/财务、[`futu_fundamentals.py`](../src/wonder_stockresearch/futu_fundamentals.py) 的基本面数据，以及 [`disclosures.py`](../src/wonder_stockresearch/disclosures.py) 的 HKEX/SEC 公告发现。`source-add` 则通过 [`sources.py`](../src/wonder_stockresearch/sources.py) 导入用户给的 URL 或文件。获取后的原始字节、可提取文本、哈希和时间信息一起归档；`source-read` 每次只返回一个片段，方便核对具体位置。

每个采集子任务还有一条 `run_steps` 记录，说明成功、失败或阻塞及其尝试次数。这张表是执行记录；草稿内容仍在 `runs.draft_json`，资料原文仍在文件里。

### DeepSeek 写了什么，Python 又检查什么？

`writer.py` 只读取该 run 已归档的资料片段和研究规则，要求 DeepSeek 返回符合 `ResearchDraft` 形状的 JSON。模型不能自行增加真实来源；它写出的 `source_id` 必须能在本次 run 的资料里找到。`write-draft` 保存草稿，但**不会调用 `finish`**。

[`research.validate_packet()`](../src/wonder_stockresearch/fixed/research.py) 检查 24 问、6 个门闸、6 个护城河维度是否齐全，引用是否存在，关键结论有没有合格的支持来源，来源在 `as_of` 时点是否可用，以及关键冲突是否解决。`validate_run()` 还检查已归档文件。六个维度都有分数时，Python 才计算 0–60 总分；有任何一个 `null`，总分就是 `null`。

校验是**程序能检查的边界**，不是对模型结论的事实证明。比如程序能发现“Q03 没有合格引用”，但不能可靠判断“Q03 的文字是否真的分析了资本开支”。这仍需要读原文和审阅报告。价格与估值也不能改变公司评分；估值四问目前尚未实现。

### 为什么还要单独 `finish`？

`finish_run()` 会重新校验草稿，才调用 [`reports.render_company()`](../src/wonder_stockresearch/fixed/reports.py) 生成 `company.md`，并导出 `research.json`。如果这次 run 指定了已完成的 `baseline`，还会生成对比用的 `review.md`。文件写成功后，程序记录它们的 SHA-256，再把 run 标成 `completed`。已完成的版本不能被新草稿覆盖；要更新判断就新开 run。

## 4. 一句话入口与开放问题

`chat` 是方便入口，业务规则仍由上面的 Python 服务执行。比如输入“帮我研究一下中国电信”，[`router.py`](../src/wonder_stockresearch/router.py) 让 DeepSeek 返回一个经 Pydantic 校验的 `RoutePlan`，[`chat.py`](../src/wonder_stockresearch/chat.py) 根据它决定走正式研究、开放问题或澄清。公司简称不确定时，[`symbols.py`](../src/wonder_stockresearch/symbols.py) 提供候选，等用户选定。

正式研究的 `chat` 路径有两次确认：先确认要启动研究，然后 `start → collect → write-draft → validate`；验证通过后，再确认 `finish`。`chat_sessions` 保存待确认内容和关联的 `run_id`，所以终端重启后可以按 session 继续。[`skill_registry.py`](../src/wonder_stockresearch/skill_registry.py) 读取项目 `skills/*/SKILL.md` 给 Router 提供上下文；Skill 文本本身不能获得数据库或网络权限。

另一条路径是 `ask` / `ask-followup`。[`agent.py`](../src/wonder_stockresearch/agent.py) 中只有**一个**交互 Agent：模型每轮选择读取归档资料、报告或 Memo 等受限工具，或给出最终回答。Python 限制工具名、参数、轮数、token 预算和重复调用，并验证最终引用。结果是临时 `InteractiveReport`，不会修改正式 run 的问答、门闸或评分。只有显式调用 `memo-save`，才会另存 `CompanyMemo`。这个 Agent 当前只读已归档资料，不会自己联网采集。

所以项目里有三种不同产物：**正式研究报告**（`finish` 后冻结）、**临时回答**（Agent 会话结果）、**长期 Memo**（`memo-save` 后保存）。把它们分开，后续追问就不会悄悄改掉一份已经完成的研究。

## 5. SQLite 与文件各管什么

```text
data/
├── wonder.db                         # 股票、run、来源元数据、步骤和会话等
├── stocks/
│   └── HK.00728/
│       ├── sources/                  # 原始资料与提取文本；文件名基于内容哈希
│       └── runs/
│           └── <UTC时间>-<run_id>/   # 完成后的 company.md、research.json
│                                    # 有 baseline 时还有 review.md
└── backups/                          # 手动 backup 的输出
```

表结构、版本迁移、SQL、写锁、原子文件写入都在 [`storage.py`](../src/wonder_stockresearch/storage.py)。可以这样理解权威关系：**SQLite 管状态和结构化草稿；来源文件保存取得的原始字节；Markdown 是从通过校验的数据渲染出的阅读视图**。手工改 `company.md` 不会改回数据库，反而会破坏完成产物的哈希校验。

`sources` 保存一份资料的元数据，`run_sources` 记录哪次研究使用了它。这样复评能沿用旧资料，同时保留它原来的发布日期和获取时间；不会把旧资料伪装成刚披露的新证据。`stocks` 是本地清单，`runs` 是正式研究版本，`agent_sessions` / `agent_events` / `interactive_reports` / `company_memos` / `chat_sessions` 分别保存交互路径的数据。

业务输出通常是 stdout 上的 `{ "ok": ..., "data": ..., "issues": [...] }` JSON；长任务的简短进度走 stderr。交互终端中的 `finish` 会显示易读的完成摘要。密钥由运行环境或项目根本地 `.env` 加载，不进入数据库、报告或日志。

## 6. 用最短路径读代码

如果你想顺着一条真实请求阅读，建议按这个顺序：

1. 在 [`cli.py`](../src/wonder_stockresearch/cli.py) 找 `build_parser()` 和 `_run_business()`，看 `start`、`collect`、`write-draft`、`validate`、`finish` 分别调用谁。
2. 在 [`research.py`](../src/wonder_stockresearch/fixed/research.py) 看 `start_run()`、`save_draft()`、`validate_run()`、`finish_run()`，理解状态怎样从 `draft` 变成 `completed`。
3. 在 [`models.py`](../src/wonder_stockresearch/models.py) 看 `ResearchDraft`、`Source`、`EvidenceRef`、`ResearchPacket`，理解传递的 JSON 长什么样。
4. 在 [`storage.py`](../src/wonder_stockresearch/storage.py) 看 `_SCHEMA`、`initialize()`、`project_lock()`；把 SQL 表与上面的对象对上。
5. 最后再读 `writer.py`、`chat.py`、`agent.py`。它们是不同的入口与写作方式，共用前面的研究规则和存储。

你可以先运行一个**只读**命令确认入口：

```bash
uv run wonder-research --help
```

若已有 run，可运行 `uv run wonder-research --root . status <RUN_ID>` 查看它的状态；`<RUN_ID>` 需换成实际编号。测试位于 [`tests/`](../tests/)，使用临时目录和假的网络/模型响应验证主要边界；测试通过也不代表每份真实资料都可取得或模型判断一定正确。

## 7. 当前范围

目前实现的是港股、美股的**公司研究**、历史、手动复评、自然语言入口和临时问答。估值 Q25–Q28、附记正文、专题报告、定时任务、Web UI 和交易功能都还没有实现。项目不读取真实账户、持仓或订单，也不执行交易。阅读早期 OpenCode 集成文档时，记住它是历史记录；当前运行链路从本项目的 Python CLI 开始。
