# 技术规格 v1

本文件给出首版实施默认值，供新会话直接实现。边界来源于 PLAN；字段、CLI 名称、超时等为工程默认值，可因实际接口验证做不改变产品语义的小幅修正并记录。首版没有模型 API 调用或密钥存储；后续直接接模型 API 的扩展边界见第 8 节，编排演进边界见第 9 节。

## 1. 模块与依赖

包 wonder_stockresearch，入口 wonder-research = wonder_stockresearch.cli:main，兼容 python -m wonder_stockresearch。

按职责逐阶段建立：models.py（契约）、research.py（服务/校验）、reports.py（纯渲染）、storage.py（SQLite/文件）、sources.py（获取提取）、market.py（富途）、disclosures.py（公告）、cli.py/__main__.py。不先生成空模块或多层接口。

Python >=3.11；Pydantic >=2,<3。sqlite3/argparse/json/hashlib/decimal/pathlib 为标准库。开发用 pytest、ruff；HTTP 用 requests，文字 PDF 用 pypdf。富途 SDK 阶段 4 锁定 `futu-api==10.11.7108`（实测可连接本机 OpenD 并返回快照/财务）。uv.lock 随实现维护。不在文档里承诺未经验证的 SDK 版本。

项目根通过 --root 显式传入，否则用当前目录，要求根目录存在本项目 pyproject.toml 标识；不要从任意 cwd 悄悄写旧项目。import 无网络/建表副作用。

## 2. 数据模型

所有输入严格校验，未知字段拒绝。ID 使用 UUID4；ticker 规范为 HK.五位数字或 US.大写标的（允许字母、数字、点、连字符），拒绝路径字符与 ..；SYNTHETIC 只允许离线 fixture 模式。UTC 存时间，Markdown 可显示 Asia/Shanghai 并注明。

核心类型（各字段必填，标 ? 为可空；列表允许空的条件见研究规格）：

- Source：id、ticker、kind、title、url?、endpoint?、published_at?、publication_precision（date/datetime/unknown）、publication_timezone?、retrieved_at、sha256、raw_path、text_path?、extraction_status、metadata（provider 字段/期间、访问结果）。URL 与 endpoint 至少有一个；用户文件允许 origin_file_label 代替二者，完成事实引用前仍需公开来源定位或披露身份。
- EvidenceRef：source_id、locator、relation（supports/refutes/context）。PDF locator 含 page，提取文本含行号或章节，provider 含 field/period。
- Statement：id、kind（fact/inference/assumption）、text、evidence[]、numbers[]。NumericFact 为 value（Decimal 字符串）、unit、currency?、period、basis、evidence[]。
- Gap：missing、where_to_find、next_check、impact。
- Answer：question_id、status（answered/partial/missing）、statements[]、gap?。
- Gate：gate_id、result（pass/fail/insufficient）、rationale（Statement）、gap?。
- Moat：dimension_id、score（int 0..10 或 null）、confidence（low/medium/high）、rationale（Statement）、gap?。
- Challenge：counterargument（Statement）、channels_checked[]、search_scope、refutation_found、overturning_evidence、next_check。
- Conflict：description、source_ids[]、critical（bool）、resolved（bool）、resolution?。
- ResearchDraft：schema_version=1、ticker、as_of、answers[]、gates[]、moats[]、challenge、conflicts[]、conclusion（Statement）、review_explanations[]。
- ReviewExplanation：target_id（问题/门闸/维度或 conclusion）、statement（Statement）；没有 baseline 时列表为空。
- ValidationIssue：code、location、message、severity（error/warning）。

缺口允许完成，但不允许借缺口继续给未经证据支持的结论。score_total 和 quality_status 由程序派生，不接受模型输入。Draft 中不接受 valuation/target_price/action 字段。

## 3. SQLite 与文件

四张初始表：

- stocks(ticker PK, name nullable, watched bool, created_at)。研究不要求先加入清单；start 自动创建 watched=false 的股票。
- runs(id PK, ticker FK, kind, parent_run_id nullable FK, as_of, baseline_run_id nullable FK, status, draft_json nullable, schema_version, created_at, updated_at, completed_at nullable, error_json nullable, artifact_hashes_json nullable)。kind 首版为 standard；附记（addendum）与专题（topic）为研究规格第 9 节预留的类型，其正文与估值字段本版不写入。parent_run_id 指向附记挂靠的标准研究，baseline_run_id 仍只用于复评比较，两者语义不同。阶段 2 的 finish 有 baseline 时只保存关联，不生成 review.md；比较与 review.md 属于阶段 6。
- sources(id PK, ticker FK, kind, metadata_json, sha256, raw_path, text_path nullable, retrieved_at)。
- run_sources(run_id FK, source_id FK, PRIMARY KEY(run_id,source_id))。

阶段 4 已实现一张轻量运行步骤表 `run_steps`（schema v2，经 v1→v2 显式迁移加入），作为 `runs` 的执行明细，不是事件溯源或通用任务队列：

- `run_steps(run_id FK, step_key, attempt, status, input_hash nullable, output_ref nullable, provider nullable, model nullable, started_at nullable, finished_at nullable, error_json nullable, PRIMARY KEY(run_id,step_key,attempt))`。
- `status` 只允许 `pending`、`running`、`succeeded`、`failed`、`blocked`、`skipped`；`attempt` 从 1 开始递增。
- `output_ref` 只指向已有的来源、草稿或受控 JSON 产物，不把大段原文或隐藏推理塞进步骤表。
- 采集器和未来 writer 使用它记录可重试边界、输入哈希、provider 错误和输出引用；`runs.draft_json` 仍是草稿的正式内容。`output_ref` 形如 `source:<uuid>`。
- 此表通过显式 schema migration（`_MIGRATIONS` v1→v2）加入，`initialize` 对已有 v1 库执行迁移，不重建已有表。

foreign_keys=ON、busy_timeout=5000；首版使用默认 journal，不先开 WAL。PRAGMA user_version 管理 schema，阶段 1–3 为 1、阶段 4 为 2；只添加显式递增迁移，不每次启动重建表。

答案保存在 runs.draft_json，SQL 不拆成 claim/evidence 图数据库。JSON 是 SQLite 的正式内容；导出 research.json 是同内容快照。原文文件是字节事实源。Markdown 为视图，手工修改不回写数据库。

文件布局：data/stocks/<ticker>/sources/<sha256>.<ext>，文本 <sha256>.txt；metadata 保留在数据库；相同字节允许复用文件，但不合并不同获取事件的 Source 记录。每个来源引用仍能追踪此次获取时间。

run 目录名由 created_at 的 UTC 秒级时间与完整 run_id 生成且固定。草稿不修改其他 run。所有数据库路径存相对项目根路径，导入文件复制到 data 后才可正式引用。

## 4. CLI 契约

共同选项：--root PATH。JSON stdout 为 {ok, data, issues}；issues 总为数组。正常/仅警告 exit 0，校验拒绝 exit 2，I/O或provider错误 exit 1；日志到 stderr，不打印秘密。JSON 输入 --input PATH，PATH=- 表示 stdin。

阶段 1 离线命令：

- validate-packet --input packet.json：packet={draft: ResearchDraft, sources: Source[]}；返回结构校验，不声称归档原文真实存在。
- render-packet --input packet.json --output PATH：先结构校验，再生成标注“离线样例/未正式归档”的报告；output 必须在 root/data/ 下。

阶段 2 起业务命令：

- watch add TICKER [--name NAME] / watch remove TICKER / watch list：只维护本地 watched，不删除历史。
- start TICKER [--as-of ISO] [--baseline RUN_ID]：默认 as_of 为当前带时区时间；产生 draft run 与 run_id，kind 固定为 standard。baseline 必须是同 ticker 的 completed run，时间不晚于新 run。不自动选未完成草稿。本版不提供附记/专题的启动开关，只保留表的 kind/parent_run_id 字段。
- status RUN_ID / history TICKER：返回状态、质量信息、来源摘要/历史元数据，不一次吐所有原文。
- save-draft RUN_ID --input packet.json：packet={draft: ResearchDraft, sources: Source[]}，沿用阶段 1 形状。保存已通过类型与身份校验的草稿，并把 packet 内来源登记到 sources/run_sources。阶段 5 起校验会把 packet 内来源与该 run 已挂来源合并（`_effective_sources`），因此宿主引用 collect/source-add 已归档的来源时 `sources` 可留空，不会误报 `unknown_source`；真正未归档的引用仍被拒绝。允许研究内容缺失，返回业务校验 issues 但不阻止保存。
- validate RUN_ID：检查结构、归档文件、时间与来源关联，不写 completed。
- finish RUN_ID：校验后由服务渲染 company.md 和 research.json；有 baseline 时只保存关联，不生成 review.md（比较与 review.md 属于阶段 6）。无需第二个人工确认命令。
- resume RUN_ID：仅 failed→draft，保留原草稿和来源；completed 拒绝。普通校验失败仍保持 draft，不滥用 failed。

阶段 3–4：

- source-add RUN_ID --input source-request.json：request 含 metadata 及 file 或 url，二者恰好一个。程序取得原始字节、计算哈希和实际 retrieved_at，不能接受用户伪造哈希/获取时间。
- sources RUN_ID：列表返回元数据与 source_id；source-read RUN_ID SOURCE_ID [--page N | --start-line N] [--limit-lines N]：默认 120 行，上限 300 行与 20000 字符，返回 locator、截断标记和下一段位置。
- collect RUN_ID [--kind all|market|filings]：all 默认。market 含报价快照与财务原始数据（富途只读）；filings 发现并归档最近年报/中期/季度报告（HKEX/SEC 公开渠道）。各任务在 `run_steps` 分列，失败/受限写 error_json 与 issues，不把 partial_success 当 complete coverage。只做数据收集，不生成评分、不 finish。至少一个子任务成功时退出 0，全部 failed/blocked 时退出 1。

阶段 6：

- compare RUN_ID：要求 baseline；生成/返回结构化差异，未完成草稿可以预览，但正式 review.md 随 finish 固化。
- backup --output PATH：创建新目录，不覆盖已有备份，目标限项目 data/backups/；输出 manifest 与恢复说明。

所有命令在模型传值前有 --help，输入错误给出字段路径和修正说明。工具能力只允许文档中列出的操作，不提供任意 Python/SQL 执行命令。

## 5. 核心服务边界与状态

阶段 1 必须有 validate_packet(packet)->list[ValidationIssue] 与 render_company(packet)->str；阶段 2 业务统一经 research.py 接入 storage。CLI 只做参数解析、调用、序列化，不藏业务规则。

finish 的顺序：取得项目写锁 → 读取并校验 run → 原子替换该未完成 run 的临时产物 → 事务写入 completed 与产物哈希。崩溃在文件生成与数据库提交之间时仍未完成，重试可安全重建；completed 重试只检查并返回原产物，哈希不符报 artifact_mismatch，不擅自覆盖。

所有业务写操作使用项目内标准库锁文件机制（macOS/Linux 用 fcntl，非目标 Windows 本版明确不支持）并有 5 秒获取超时；不持锁进行网络下载，下载完成后再锁定登记。下载暂存于 data/tmp，最终关联前重新检查 run 可写。

provider 错误记录来源收集 issue，可继续其他资料。未处理的持久化/渲染错误将未完成 run 标 failed（数据库自身不可用时仅 stderr 明确记录，不能声称状态成功保存）。resume 不重放此前网络请求。

## 6. 网络、时间与财务默认值

requests 连接超时 10 秒、读取超时 90 秒；网络或 429/5xx 最多 2 次尝试，尊重 Retry-After 并限制等待到 60 秒，超出则可重试错误；其余 4xx 不自动重试。单文件上限 50 MiB，流式下载检查实际字节；只接受 HTTP(S)，限制重定向 5 次，禁止从公开 URL 跳到回环/私网地址。OpenD 是 market.py 单独配置的本机连接，不经 URL 下载器。

不执行下载的脚本、宏或 HTML；提取失败保留原文并明确标状态。PDF 扫描件提示人工补文本，不自动安装 OCR。

公告发现默认回看 3 年，最多 20 条年报/中期/季度及业绩相关候选，自动保存最近 3 个完整年度年报和最新一期业绩材料（以实际可取得内容为准）。HKEX 用官方 `prefix.do`/`titleSearchServlet.do`，检索 `rowRange` 放宽后过滤相关类别；SEC EDGAR 需环境变量 `WONDER_SEC_USER_AGENT`（SEC 要求带联系方式），未设置时该通道标 blocked，不影响其他能力。十年问题仅凭三年资料不得标完全回答；按问题需要经宿主补历史来源，记录额外检索范围。

provider 来源应另外保存 observed_at 或原始披露的时间依据：retrieved_at 是取得时间，不能当作历史数值的发布时间。start 固定 as_of 后取得的新报价，若 observed_at 晚于 as_of，只保留为本次下载结果，不关联为该 run 的有效证据；公司研究可继续，不能悄悄前移截止时点。财务快照如能通过已归档的一手披露核对发布时间和具体数值，可引用该披露；不能仅因报告期早于 as_of 就认定晚取得的修订数据当时已可用。无法确定时间的 provider 数值保留为缺口/背景。

富途财务请求记录数不等于完整财年数。期间连接必须包含 ticker、period_end、period_type、currency、accounting_standard；原值和规范化值分开，不猜单位。本版优先展示原始可靠指标；遇到金融企业不硬套工业企业 ratios。时间可用性按研究规格。

## 7. 测试与交付

测试至少覆盖模型约束、缺分值、来源关联与未来资料、持久化恢复、不可覆盖、失败重试、报告导出与基准比较。fixture 无真实账户/秘密，网络函数被 fake 替换。富途 SDK lazy import；不依赖真实 OpenD 的 CI。

第 1 阶段实际建立后应能运行 uv run pytest 和 uv run ruff check .；把确实运行的结果写入 PROGRESS，不预先填写通过。每阶段文档需使用真实符号和已运行命令。

备份验证：用 SQLite backup API、一份 sources/与completed run 文件清单及 SHA-256 manifest；恢复演练创建项目内临时根，验证 schema、外键、原文哈希和 completed 产物。暂不实现云同步、自动定时备份或事件重放。

## 8. 后续直接调用 LLM 写报告（非首版实现）

用户明确希望以后能接 DeepSeek 等模型 API。首版保持研究模型、证据、存储、校验和渲染独立于宿主；本节记录扩展接点，不要求现在创建空接口、SDK 依赖或 provider 注册框架。

建议未来新增一个小型 writer 模块：从现有 run、研究规则与带 source_id/locator 的原文片段构造上下文，模型返回 ResearchDraft，再调用现有研究服务保存与验证。写作与数据采集分开；仅给已有材料写作不需要完整 Agent 循环。将来若需模型自己搜索和调用工具，再单独设计该执行过程。

- 输入：run_id、明确选择的模型配置，以及从本地规则和归档材料构造的上下文；不依赖读取 OpenCode 私有会话数据库。
- 输出：满足既有 schema 的 ResearchDraft；模型不能新造 source_id、修改来源日期或跳过门闸/反证要求，评分总分仍由 Python 计算。
- 单供应商起步，DeepSeek 为用户举例的优先候选；API 格式兼容不代表结构化输出、工具、参数完全相同，实际接入时依官方文档和测试确认。
- 设置明确超时、输出限制和调用预算；不合格 JSON/schema/引用返回错误与草稿，不无限自动重试、不因修复失败强行 finish。
- 用户在运行环境自行设置凭据。程序可读取环境变量用于请求，但不得持久化或输出密钥/请求认证头；对话中不得索取秘密。不新增 .env 密钥文件到仓库。
- 记录供应商、模型、提示模板版本及可取得的用量摘要，禁止保存隐藏推理或敏感认证内容。
- 测试使用 fake LLM 返回正常草稿、非法 JSON、虚假引用和超时，确认失败不生成 completed 报告；真实 API smoke 在用户要求该阶段且运行环境准备好后进行。

直接接模型不会自动赋予浏览、PDF 提取、网络搜索和数据库写入能力；这些仍由现有 Python 工具与显式流程提供。是否加入自主工具循环需独立决策。

核对入口：[DeepSeek 官方 API 文档](https://api-docs.deepseek.com/zh-cn/)。本次只确认存在 API 接入途径，未实际调用或验证任何特定模型效果。

## 9. 编排与宿主边界（2026-09-20 决策）

### 9.1 默认路径

- 领域逻辑继续使用普通 Python 函数、小模块和显式事务。
- CLI 是稳定的宿主无关接口；OpenCode 通过 skill、受限命令或薄工具调用 CLI，不成为数据库或研究报告的权威来源。
- OpenCode 会话可以中断，但研究必须能从 `run_id`、SQLite、来源文件和正式产物重建；不能只依赖聊天历史。
- 只有在已归档材料上生成结构化 `ResearchDraft` 时，优先使用小型 writer；不为了单次模型调用引入图编排。

阶段 5 已实现：project skill 正文位于 `skills/company-research/SKILL.md`，由项目根 `opencode.json` 的 `skills.paths: ["skills"]` 注册，保持唯一正文；宿主只调用第 4 节列出的 CLI，不新增命令、不接 MCP 或模型 SDK。修改 skill 或 `opencode.json` 后需重启 OpenCode 生效。

### 9.2 LangGraph 的启用条件

LangGraph 只能作为阶段 7 之后的可选 orchestration 包，启用前必须有真实运行证据表明至少存在两项问题：动态工具选择、多轮补证据、跨天恢复、独立问题并行，或人工监督流程难以审计。它的节点只能调用现有业务服务；不能直接写 SQLite、修改来源元数据、计算护城河总分或绕过 `validate`/`finish`。

LangGraph 状态只保存 `run_id`、步骤键、结构化中间结果和输出引用；SQLite/文件系统仍是事实来源。若实际需求只是单次结构化写作，继续用 writer；不要以 LangGraph 替代现有 `runs` 和来源归档。

### 9.3 暂不采用的编排系统

Temporal、Prefect 和多 Agent 框架暂不加入首版。只有出现后台调度、远程 worker、多用户并发或跨基础设施长期运行需求时，才重新评估基础设施级 durable execution；这属于产品范围变化，需要单独决策和验收。
