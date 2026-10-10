# 记忆、存储与 Agent 架构调研（2026-10-06）

本次是现状审计和下一步建议，不是阶段 22 功能交付。建议尚未替代 PLAN 中的阶段安排。本次没有修改业务代码、迁移主库、清理历史或调用模型/OpenD；新增的检查产物只在 `data/audits/20261006-architecture/`。工作区已有大量修改，本报告以当前工作区而非 Git HEAD 为准。

后续：2026-10-07 已完成建议第一项 19D.1 可靠性收口，见 [执行与恢复](23-agent-runtime-reliability.md)。下列统计与问题保留为 2026-10-06 审计快照，后续测试/迁移状态以新文档和 PROGRESS 为准。

## 1. 结论与实际验证

建议保留 Python、CLI、SQLite、固定研究 workflow 和单交互 Agent。最值得投入的是事务一致性、运行时收口、中文检索、可重建索引和评测。当前没有证据支持引入外部记忆平台、向量库、图数据库或多 Agent 能显著改善本项目。

实际执行：

- `./.venv/bin/python -m pytest -q`：190 passed，1 failed，耗时 2.53 秒。失败为 `tests/test_stage16_quality.py::test_model_call_budget_stops_before_fifteenth_call`。
- `./.venv/bin/ruff check src tests`：All checks passed。
- 对主库使用 SQLite URI `mode=ro` 与 `query_only=ON`：`quick_check=ok`，`foreign_key_check` 无错误；统计表行数、dbstat 空间和查询计划。没有调用业务 `initialize`，因此没有触发主库迁移。
- 内存 SQLite 的中文 FTS 实验：文本“中国移动的资本开支下降，ROE 稳定。”中，当前 `unicode61` 搜“资本开支”和“中国移动资本开支趋势”均零命中；搜“ROE”有命中。这证明词法检索缺口，不代表所有中文查询都失败。
- 在独立合成项目中注入临时报告写入失败：Session=completed、Turn=succeeded、Attempt=succeeded，但报告数为 0。结果在 `transaction-probe.json`。这是真实不一致路径，不是仅按文件长度推测。

范围覆盖规格、主要模块职责和调用链、记忆/检索、数据库迁移与 SQL、固定 Writer/校验/finish、交互执行/恢复、工具与 Skill、计算隔离、备份和现有测试。不是逐行穷尽证明，也未验证真实模型回答质量、网络服务或性能收益。

当前还有版本错位：文档主要描述 schema v10/阶段 19C，代码 `storage.SCHEMA_VERSION=11`，并已有 Attempt、respond/retry、按 Turn 保存报告与 ScriptedAgentModelClient；本机主库实际仍为 v9。因此不能把“文档写着已实现”“代码里已有函数”和“主库中已使用”混为一谈。19D 应先核验并收口已有实现，不要重新从零实现。

## 2. 项目现在怎样记忆

| 层次 | 实际实现 | 用途和边界 |
| --- | --- | --- |
| 项目开发约定 | AGENTS、PLAN、规格、PROGRESS | 给开发者的工程上下文；不等于运行时公司事实记忆 |
| 方法与权限 | Skill、Policy、Tool Registry | 方法按需加载；权限由 Python 检查，不由记忆授予 |
| 入口状态 | chat_sessions | 自然语言确认、消歧和恢复；历史只保留最近 20 条 |
| 执行历史 | Session/Turn/Attempt、agent_events | 对话容器、一次目标和一次执行；代码已开始保存运行时历史 |
| 工作记忆 | agent_scratchpads | 每个 Session 一行，版本递增；保存目标、观察、引用、计算和待查问题 |
| 临时回答 | interactive_reports + episode | 按 Turn 保存结果；episode 标 provisional，供定位历史 |
| 用户保存的观察 | company_memos | memo-save 才写入；同 ticker/title 新增版本 |
| 长期检索投影 | memory_entries + memory_entry_fts | completed 报告、Memo 和 episode 的可查询投影 |
| 原始证据 | sources/run_sources、归档原文、source_page_fts | 证据身份、获取时间、字节哈希与页/行定位；这与回答记忆不同 |

主要实现是 `agent_memory.py`、`agent._initial_context()`、`storage.search_memory_entries()`。

每次交互开始会同步该公司的完成报告和 Memo，检索最多五条记忆，并加载 Scratchpad 与上一份临时报告。工具调用把结果前 1,400 字符写为观察；保存最近 12 条观察、12 条计算引用、24 条来源引用和 8 个待查问题。消息总字符达到 28,000 时，压缩为 system prompt、原问题和 Scratchpad。

长期投影的报告只保留 Markdown 前 30,000 字符；注入上下文的每条记忆最多 2,400 字符。检索先按空白切词执行 FTS OR 查询，命中后按更新时间排序；零命中则返回该公司的近期记录。它没有真正的中文语义检索，也没有版本失效或按研究截止时间过滤。

本机主库现在 `memory_entries=0`、`agent_scratchpads=0`、`company_memos=0`、`analysis_runs=0`，但有两份旧交互报告和六个 completed run。因此“记忆能力在代码里”成立，“现有主库已经积累大量公司记忆”不成立。索引是惰性同步的，也不能仅凭空表判定功能坏了。

### 最需要改进的记忆行为

1. **让工作状态属于当前 Turn。** `ensure_scratchpad()` 遇到已有 Session 记录直接返回；follow-up 起始时可能仍带旧目标和已清空的待查问题。应保留 Session 摘要，但为新 Turn 建立新的目标/待查项，来源回执仍按 Turn 约束。
2. **压缩时保留最近交互。** 当前压缩会丢掉所有近期消息，观察只是 JSON 前缀，参数、末尾数值、用户补充或错误修复可能消失。改为“结构化摘要 + 最近一到两轮 + 工具产物 ID”；数字、期间、口径和 locator 用结构化字段保留。压缩后重新检查预算，system prompt 也计入。
3. **相关性与近期回退明确区分。** 返回 `match_kind`、关键词/得分和回退原因；无相关记录可以返回空。不能把“最近”包装成“相关”。先增加领域词表/别名和中文切词，再比较 trigram；trigram 对短于三个字符的词需要补充方案，索引体积也要测量。
4. **按问题单元检索报告。** 用 ResearchDraft 的 Q/G/M 单元生成小块，附 target_id、run/as_of、准确来源，而不是只检索 Markdown 开头。检索片段应围绕匹配位置，不总是取前 2,400 字符。
5. **增量更新。** 现有同步每次重读所有 completed Markdown，且 upsert 总会删除、重插 FTS。利用现有 content_hash、模板/索引版本，仅对变化的内容更新；通过显式重建入口修复缓存，普通查询不隐式大量写入。
6. **把保存资格和事实可信度分开。** `trusted` 目前表示“完成报告/用户保存”，不意味着其中推断已被证实。保留来源类型、fact/inference/assumption、审核状态和是否可支持当前回答。旧报告的 `formal_report_source` 只是 context 索引，不能当成原文定位。
7. **版本关系与时间。** Memo 旧版本保留，但默认检索当前版本；历史查询再取旧版。后续事实记忆加入 evidence_as_of、valid_from/valid_to、recorded_at 和 supersedes，不覆盖历史结论。日期未知的 Memo 只能作为历史观察。

这些前六项主要修正已有记忆和上下文管理。完整事实记忆、自动抽取/冲突管理及专项评测仍可留在阶段 22，不把全部范围塞入 19D。

## 3. 主流方案与可借鉴部分

调研使用官方文档和公开源代码，按 2026-10-06 可访问版本说明；没有依据厂商自报跑分判断谁“最先进”。未在本项目做同条件性能比较。

| 方案 | 官方设计重点 | 对本项目的价值与采用判断 |
| --- | --- | --- |
| [LangGraph Memory](https://docs.langchain.com/oss/python/concepts/memory) | thread 内状态/checkpoint，跨 thread 的命名空间 store；区分事实、经历和方法记忆 | 借鉴 scope 与生命周期即可；这不要求把函数核心换成图编排 |
| [Mem0](https://docs.mem0.ai/core-concepts/memory-operations/add) | 从消息抽取记忆，标识隔离、metadata、相关性检索和可选到期；原样与推断模式有不同重复风险 | 适合偏好/摘要记忆；公司事实还需要 period、币种、定位、截止时间和可审计写入，不宜直接自动采纳模型抽取 |
| [Letta 当前 Memory](https://docs.letta.com/agent-sdk/memory) | 当前 SDK 已描述 git-backed MemFS；system 记忆常驻，其他文件按需读取；可配置后台整合 | 借鉴小型常驻摘要与按需历史。V1 memory blocks 已列为 legacy，不能把旧 MemGPT 介绍直接当当前完整方案；不引入其后台子 Agent/宿主 |
| [Zep/Graphiti](https://help.getzep.com/graphiti/getting-started/overview) | 随 episode 演进的实体/关系/事实；时间、文本、语义及图搜索 | 最值得借鉴的是事实时间与失效保留历史。现阶段单股研究不需要先建设整张实体关系图 |
| [Anthropic 上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | 高信号的小上下文、按需获取、压缩与结构化笔记 | 直接改进现有 Scratchpad/Context Builder，不需换供应商或安装平台 |

建议路径是 **SQLite 原始证据与版本历史 + 小型工作摘要 + 相关片段检索 + 显式保存 Memo**。后续仅在离线召回集显示词法方案不足时，重新决策 embedding/hybrid retrieval；目前向量库仍不在已确认范围内。

## 4. wonder.db 的东西是否必要

只读统计原始记录在 `data/audits/20261006-architecture/db-inventory.json` 和 `db-detail.json`。

数据库 82,411,520 字节，即 **78.59 MiB**。page_count=20,120，freelist_count=0，默认 DELETE journal。空闲页为零不等于所有页面没有内部空隙；仅跑 VACUUM 不能解决重复内容，也不是本次首要动作。

| 内容 | 实际规模 | 是否保留、怎样优化 |
| --- | --- | --- |
| stocks/runs/sources/run_sources | 6 股票、28 run（6 completed/22 draft）、398 Source、465 关联 | 必要的权威记录。Source 有 279 个唯一字节哈希；重复获取记录不应合并，否则会破坏获取时间和引用身份 |
| source_page_fts 及内部表 | 15,365 页/片段；65.40 MiB，占主库 83.22% | 搜索必要，重复正文不必要。优先按内容复用索引 |
| source_conflicts 及索引 | 18,673 行，7.69 MiB | 全部是 provider_unverified，零 matched/conflict。它实际还承担逐字段核对账本，不应把行数叫成真实冲突数 |
| draft_sections | 170 行，表约 2.36 MiB | 断点缓存能省真实模型费用；保留正在使用的输入哈希，旧缓存可显式归档/清理 |
| draft_reviews | 59 行，表约 0.75 MiB | 审核历史与 finish 门闸必要；冻结报告关联的审查永久保留 |
| run_steps | 245 行，56 failed/3 running/186 succeeded | 重试与失败诊断必要；识别失去执行进程的 running，不能把所有 running 自动判失败 |
| traces/trace_spans | 38 trace/210 span；主要表合计约 0.18 MiB | 目前很小；可配置保留期，但不能先拿它当数据库膨胀主因 |
| Agent/Memo/Memory/analysis 表 | 多数为空或极少 | 空表不是主要开销；语义独立，通常不值得为了减少表数合并 |
| FTS 的 `_data/_idx/_content/_docsize/_config` | SQLite 自建影子表 | 由 FTS 管理，不能单独删。表多不等于多设计了同样多的业务模块 |

### 最有价值的瘦身：索引按内容复用

当前原文文件已经按哈希去重，但 `_ensure_source_page_index()` 按 source_id 索引。88 个已索引获取记录只有 43 个不同的公司/字节哈希。按 `ticker + sha256 + locator + content_hash` 去重，15,365 条可变为 **7,351 条**；正文字符 35,639,044 可变为 18,739,297。这是去重潜力，不是已实现的磁盘节省。

建议增加最小的 document/chunk 关联：正文片段按原文字节哈希、提取器版本与 locator 复用；每个 Source 获取事件映射回共享文档。检索先限制本次 run 的 Source，再选文档，最后返回当前 run 合格的 source_id 和 locator。不能把同文档另一取得时间的 Source 随意当本次来源。

[FTS5](https://www.sqlite.org/fts5.html) 的 external-content 可以让结构化 chunk 正文成为唯一 DB 副本，FTS 仅保存索引；但需要保证内容表和索引同步。对 memory_entries 尤其合适，因为目前普通表与 FTS content 都存同一段正文。对于页索引，只设置 external-content 并不会消除文件中本来已有的正文副本；需要结合共享 chunk 身份，不能单独承诺节省固定百分比。

不建议马上分出第二个数据库。当前先保留一库，完成内容去重、批量事务与可重建契约；若之后缓存体积和重建时间实测影响备份，再评估独立检索库。

### 核对记录与索引

`source_conflicts` 的未核对字段可继续保留在按来源的核对产物中，主库保存数量、状态、产物路径/哈希；真实 conflict 保留详细行。是否压缩必须经过迁移与查询契约验证，不能直接删除 18,673 行。低风险第一步是改界面统计和命名，让 unverified 与 conflict 分开显示。

只读查询计划显示按 ticker 取来源是全表扫描并临时排序；memory 默认按 updated_at 排序，现有索引却主要是 ticker/created_at。可按真实 SQL 增加 `sources(ticker,retrieved_at,id)`、`sources(ticker,sha256)` 和匹配记忆排序的索引，检查 EXPLAIN 与耗时。398 个来源现在不构成数据库性能危机，不需要泛化加索引或立即改 WAL。

### 数据目录比主库更值得观察

检查时 `data/stocks` 约 337.6 MiB，`data/tmp` 约 415.4 MiB，`data/backups` 约 83.9 MiB。tmp 中包含隔离验收副本和可复现计算产物，不能按目录名全删。

将数据分为永久权威产物、可重建缓存、诊断记录和短期临时文件；新增规划中的 `storage-stats`、`cache-rebuild`、`cleanup --dry-run`，先给出被引用/可回收清单，再显式执行。上述命令目前不存在。被报告、Memo、回执或未完成任务引用的分析产物必须保留。

备份当前复制原文和 completed run 文件，但没有复制 QueryWorkspace 的 snapshot/SQL/函数/完整结果。恢复数据库后可能剩下指向缺失分析文件的记录。这是新功能加入后出现的备份覆盖缺口。备份应遍历明确登记的引用产物，去重复制并检查哈希；不把整个 tmp 无条件打包。

## 5. Agent 与代码架构的优先问题

### P0：事务边界和执行一致性

`storage.connect()` 使用 `isolation_level=None`；`open_db()` 只关闭连接，不包事务。`agent._finish_attempt_and_turn()` 文档写“原子完成”，实际依次提交 Attempt、Turn、Session、Report、Memory/FTS。隔离故障注入已复现成功状态与缺失报告共存。

项目锁解决协作写入顺序，不能回滚前面已提交的 SQL。应为这些数据库不变量增加小型显式事务；Attempt 创建、最终提交、Memo+记忆投影和主表/FTS 更新各有清楚边界。不要持数据库事务进行 HTTP、模型或计算。正式 `finish_run` 已有显式完成事务，保留其文件生成后提交状态的做法。[SQLite 事务语义](https://www.sqlite.org/lang_transaction.html)。

当前模型调用消耗会更新 Turn/Session，但 Attempt 主要在结束时写累计消耗；异常崩溃/KeyboardInterrupt 后需要识别 running Attempt、保存进度并允许受控中断恢复。重试不重置预算。恢复时优先引用成功产物，不只注入上一份临时回答；不能自动回放外部请求。

### P0：预算契约与代码/文档漂移

Writer 当前常量 `MAX_MODEL_CALLS=15`，测试与技术规格要求第 15 次前停止（即最多 14）。这是明确契约分歧，不是“测试环境的问题”。先确定原 14 次约束是否保持，再同步代码/测试/规格，不能只改断言让测试绿。

Agent 的 token_budget 在调用前检查已用量，调用后可以跨过预算并直接接受 final；模型请求的 max_tokens 没有绑定 Turn 剩余预算。必须明确这是软预算还是硬上限。如保持硬预算，调用前估计输入，限制输出，并为计量误差留余量；调用后完整记账，不把未知 usage 当作零成本。失败请求是否算一次调用也要定义。

### P1：检索与证据验证统一

Fixed Research 已使用页级 FTS，而 Agent `_search_sources()` 仍逐文件读全篇、子串搜索，每篇只取第一次命中、最多五篇。统一低层“归档片段检索”函数，保留 run/ticker、as_of 和 locator 限制；同一文档应能返回多个相关片段。

最终引用的当前 Turn 回执检查仅在明确 tool_names 含 `run_readonly_sql` 时启用；legacy 直接 ask 与其他 Skill 仍主要检查来源存在/归属。建议先为新 Session 统一实际阅读回执要求，保留旧 Session 兼容；用户提供的 locator 本身不证明 Agent 读过那段原文。

`source_policy` 的数值核对按 metric/period/currency 配对，未完整纳入单位、期间类型和会计口径，且缺币种时可能进入宽松匹配。应要求可比元数据齐全或明确无法核对，多候选不能任取第一条。来源发布时间与报告期末也应保持不同语义；这是数据准确性改善，需专门 fixture，不能通过调评分修正。

### P1：工具协议与可恢复错误

现有 Tool Registry 有 schema/allowlist/handler，但注册 handler 后仍回到 `_execute_tool_impl` 的长 if 链；新增工具还得改同一个分发器。改为每个函数一次显式绑定即可，无需通用插件框架。

目前 QueryWorkspace 错误可以回给模型，普通参数/工具错误多数结束 Turn。为明显可修正的参数错误提供有界结构化反馈，与未知工具/越权拒绝区别处理。重复查询错误检测现在按 `(tool, error_code)`，不同 SQL 的同类错误也会提前停止；应按归一化输入哈希与错误码检测“原样重复”，连续失败另计配额。

保持当前 JSON 决策协议即可。供应商原生 tool calling 可以作为之后的对照实验，先通过模型适配层隔离，并用同一 Pydantic/执行权限/证据校验；不能仅换 API 格式就宣称更可靠。

### P2：按变化理由拆模块

当前约 16,297 行生产 Python；storage 2,143 行、research 2,008 行、writer 1,903 行、agent 1,578 行。行数只是阅读负担，真正要拆的是混合职责：`_execute_session` 单函数 373 行，同时处理模型、预算、状态、工具、回执、错误和报告。

建议逐步形成以下边界，并维持公共 CLI/API：

- Agent lifecycle：Session/Turn/Attempt、合法状态、事务提交；loop 只组织一步决策。
- Agent context：检索、Scratchpad、最近交互、压缩与上下文预算。
- Tool handler：小函数直接绑定 Registry，工具结果用一致的状态、预览、artifact_ref 和 evidence_refs。
- storage：核心研究、Agent 数据、检索、trace 的 SQL 按领域拆小模块；仍直接 sqlite3，不增 Repository 接口层/ORM/DI。
- writer：Prompt/解析、分组写作与审查；保留输入哈希和定向修订。
- 模型 HTTP：当前 Agent/Router 依赖 Writer 私有请求函数；抽出少量公共 DeepSeek 请求与安全 trace 函数，已有 AgentModelClient 继续作为交互适配，不建多 provider 平台。
- 归档财务标准化：fixed_quality、source_policy、AnalysisWorkspace 与 QueryWorkspace 应复用一致的期间/币种/单位/口径解析；避免一个路径已修正、另一路仍错误。

旧 JSON AnalysisWorkspace 与新 QueryWorkspace 暂时并存合理，但应明示兼容入口、冻结旧能力、给出迁移样例；别继续同时扩展两套 DSL。不是为短文件数量而拆文件，也不在可靠性修复时顺便大规模重写。

### 当代 Agent 设计的可借鉴原则

[Dexter 当前 loop](https://github.com/virattt/dexter/blob/main/src/agent/agent.ts) 和 [Scratchpad](https://github.com/virattt/dexter/blob/main/src/agent/scratchpad.ts) 已包含上下文压缩、工具结果预算及摘要后的新工具结果保留；借鉴“持久记录与本轮上下文不同”。[Pi Agent Core](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md) 的 transformContext 与消息转换是独立步骤，可对应 Python Context Builder。

[Anthropic 2026 Managed Agents](https://www.anthropic.com/engineering/managed-agents) 区分 session、harness 与执行环境，并让持久状态独立于易失上下文。适用于本项目的是这三个责任边界和可恢复引用，不是复制云服务、事件重放、后台队列或多 Agent。

本项目正式报告固定流程、单开放 Agent、按需 Skill、计算优先、隔离快照及引用验证方向合理。应保留这些设计；目前运行时不变量和检索质量比框架数量更决定效果。

## 6. 下一步计划（建议，尚未实施）

一次只交付一个教学阶段。优先利用已有 19D 工作区代码，而不是重新搭框架。

| 顺序 | 范围 | 必须验收的结果 |
| --- | --- | --- |
| 1：19D.1 可靠性收口 | 预算契约、显式事务、Attempt 进度/中断、旧库迁移、报告历史 | 故障注入不产生 succeeded 无报告；重试不增预算；v9→v11 在副本验证；completed 研究哈希保持；全量测试通过；文档与代码一致 |
| 2：现有上下文与检索修正 | Turn 工作目标、保留最近消息、相关片段、中文关键词与回退标签、增量记忆同步 | 新问题不带旧目标；用户补充压缩后仍在；资本开支/资本性支出、ROE 等固定样例命中；无关查询不伪装相关；旧判断不成为原始证据 |
| 3：存储与备份维护 | 只读统计、共享文档/页索引、主表/FTS 同步、按引用备份、dry-run 清理 | 同一内容多获取仍保留 Source；相同定位/内容片段只索引一次；重建后引用等价；恢复可读分析产物；不删除任何冻结报告依赖 |
| 4：19E 评测基线 | scripted 场景、真实模型独立 smoke、错误分类、质量/成本/延迟 | 同场景比较引用、计算、工具选择、恢复、预算及正式研究隔离；记录输入版本和重复次数，不用单次成功当稳定性证明 |
| 5：有基线后的模块整理 | lifecycle/context/handler、SQL 模块、DeepSeek HTTP 公共薄层、财务解析复用 | CLI JSON/退出码、冻结规则与 Skill allowlist 不变；重构前后同场景行为一致；减少实际耦合而非只减少单文件行数 |
| 6：阶段 22 事实记忆 | 版本化 claim、有效时间、被替代/撤销关系、历史查询 | period/币种/单位/口径/精确证据齐全；新事实不覆写旧版本；记忆专项召回/过时/冲突评测通过；仍禁止自动修改正式评分 |

第二、三步若需提前占用原阶段安排，应在开始实施时同步 PLAN/规格和 PROGRESS，明确只是已有能力修正还是范围扩展。完整版本化事实记忆仍后置，embedding/图数据库需要额外决策与实测收益。

第一步的最小修改清单建议为：`agent.py`、`agent_runtime.py`、`storage.py`、必要的 Writer 预算位置、Agent/迁移/故障注入测试及相应教学文档。先修复测试红灯与复现的不一致，再扩展上手包装。

评测应区分任务是否完成、引用是否真实、数值是否正确和过程预算；主观完整性再由人工抽样。可借鉴 [Anthropic Agent Evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)，但不用厂商基准代替本项目验收。

本次现有文件、SQLite、pytest 和官方源码工具足以完成审计，没有安装额外 Skill 或扩展。后续若产生确切需要再选工具，不为“优化架构”先引入新的依赖。
