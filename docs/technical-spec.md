# 技术规格 v1

2026-10-09 更新：用户已批准 Python 领域核心 + 项目自带 Pi SDK 问答运行时。Pi 路径已实现并在验收，默认切换须待质量门槛通过；安装、职责和已知限制见 [Pi 运行时](30-pi-agent-runtime.md)。本更新覆盖下文历史阶段中的“暂不引入 Pi runtime”限制。

本文件给出本版本实施默认值，供新会话直接实现。边界来源于 PLAN；字段、CLI 名称、超时等为工程默认值，可因实际接口验证做不改变产品语义的小幅修正并记录。阶段 7 的直接 DeepSeek 写作边界见第 8 节，阶段 8–10 的独立交互 Agent 边界见第 10 节，LangGraph 编排演进边界见第 9 节。

2026-10-09 可靠性修复覆盖下文旧预算默认：新 `InteractiveQuestion` 默认 `max_iterations=10`、`token_budget=60000`（允许 256–60000）；历史 Turn 的 budget_json 和显式较小输入不改。Pi 专用 `input_token_limit=16000`（1024–16000）、`output_token_limit=4096`（64–8192）与累计预算一起固化在新 Turn，重试不刷新。旧 Turn 缺少新字段时沿用原 Pi 8000/4096；旧 Python loop 的单轮输入/输出策略不变。新配置不涉及 Writer/Router，thinking 仍关闭。工具 registry v6 增加公司查询筛选与分页；紧凑 ref_id 只在 Pi 模型协议内使用，展开后继续原 Pydantic/来源范围/回执校验，对外报告仍为 EvidenceRef。详见 [可靠性说明](32-pi-agent-reliability.md)。

## 1. 模块与依赖

包 wonder_stockresearch，入口 wonder-research = wonder_stockresearch.cli:main，兼容 python -m wonder_stockresearch。

按职责逐阶段建立：models.py（契约）、research.py（服务/校验）、reports.py（纯渲染）、storage.py（SQLite/文件）、sources.py（获取提取）、source_policy.py（来源等级/核对）、market.py（富途行情）、futu_fundamentals.py（富途基本面变量）、disclosures.py（公告）、writer.py（阶段 7 DeepSeek 写作）、agent.py（交互 Agent 会话入口与有限循环）、agent_policy.py（不可变策略与动态 system prompt）、agent_tools.py（Tool handler）、agent_memory.py（Scratchpad/压缩/可信记忆）、agent_errors.py（受控错误）、analysis_workspace.py（受限临时计算）、capabilities.py（受控 capability 定义）、tool_registry.py（Agent 工具 schema、capability 与 handler）、skill_registry.py（项目 Skill Catalog 与按需加载）、router.py（自然语言 RoutePlan）、chat.py（自然语言会话和确认）、progress.py（进度事件）、envfile.py（加载项目根 .env）、cli.py/__main__.py。不先生成空模块或多层接口。

Python >=3.11；Pydantic >=2,<3。sqlite3/argparse/json/hashlib/decimal/pathlib 为标准库。开发用 pytest、ruff；HTTP 用 requests，文字 PDF 用 pypdf。富途 SDK 阶段 4 锁定 `futu-api==10.11.7108`（实测可连接本机 OpenD 并返回快照/财务）。uv.lock 随实现维护。不在文档里承诺未经验证的 SDK 版本。

2026-10-10 执行架构整理覆盖上文 Agent 分工：`agent.py` 保留公开入口，`agent_session.py` 管生命周期/事务，`agent_execution.py` 管共用预算/工具/答案接收；旧 JSON 循环在 `legacy_agent.py`，Pi 适配在 `pi_agent.py` / `pi_attempt.py`。`tool_contracts.py` 定义参数，Registry 直接绑定 handler，`agent_results.py` 管内部结果与模型投影。`model_request.py` 提供公共 Python 模型传输，Router/问答不依赖 Writer 私有请求。

新 Turn 保存运行时，恢复以快照选择，未知版本在 Attempt 前拒绝；legacy/Pi 共用请求前持久化预留、严格非负整数 usage 结算与最终事务。`pi_protocol.py` / `pi_bridge.py` 管 JSONL v1：先握手，校验请求/回复和有序 ID；原生工具前后钩子、等待型 subscribe 和 transformContext 调用 Python 规则。致命业务异常终止 bridge/Node，不依赖钩子抛错停机。内部 ToolOutcome 不改变对外报告 JSON。默认运行时与上下文选择策略、真实额度不变，详见 [实际实现与验证](35-agent-execution-architecture.md)。

同轮用户追加确认并实施分包：`fixed` 管固定研究，`interactive` 管共用问答规则与工具，`interactive.pi` 管 Python/Pi 适配；公开 agent/CLI、models/storage、数据源、公司库与模型传输仍在根包。`financial_calculations.py` 管共用财务输入和原确定性公式，无固定/交互反向依赖。`pi_runtime/src/runner.ts` 使用 SDK 类型与 strict 检查，构建为相邻 `runner.mjs`；开发依赖锁在 npm lock，运行时使用已构建 JS 与生产依赖，不要求编译器。源码/生成文件一致性由 `npm run check:generated` 检查，命令与 wheel 均沿用同一运行文件。

项目根通过 --root 显式传入，否则用当前目录，要求根目录存在本项目 pyproject.toml 标识；不要从任意 cwd 悄悄写旧项目。import 无网络/建表副作用。

## 2. 数据模型

所有输入严格校验，未知字段拒绝。ID 使用 UUID4；ticker 规范为 HK.五位数字或 US.大写标的（允许字母、数字、点、连字符），拒绝路径字符与 ..；SYNTHETIC 只允许离线 fixture 模式。UTC 存时间，Markdown 可显示 Asia/Shanghai 并注明。

核心类型（各字段必填，标 ? 为可空；列表允许空的条件见研究规格）：

- Source：id、ticker、kind、title、url?、endpoint?、published_at?、publication_precision（date/datetime/unknown）、publication_timezone?、retrieved_at、sha256、raw_path、text_path?、extraction_status、metadata（provider 字段/期间、访问结果）。URL 与 endpoint 至少有一个；用户文件允许 origin_file_label 代替二者，完成事实引用前仍需公开来源定位或披露身份。
- EvidenceRef：source_id、locator、relation（supports/refutes/context）。PDF locator 含 page，提取文本含行号或章节，provider 含 field/period。
- Statement：id、kind（fact/inference/assumption）、text、evidence[]、numbers[]。NumericFact 为 value（Decimal 字符串）、unit、currency?、period、basis、evidence[]。
- Gap：missing、where_to_find、next_check、impact。
- Answer：question_id、status（answered/partial/missing）、statements[]、gap?。`answered` 的 gap 必须为 null；`partial`/`missing` 必须有完整 gap。该状态语义由 writer 缓存复用和 `validate` 双重检查。
- Gate：gate_id、result（pass/fail/insufficient）、rationale（Statement）、gap?。
- Moat：dimension_id、score（int 0..10 或 null）、confidence（low/medium/high）、rationale（Statement）、gap?。`score=null` 保留“尚未评估”的原始语义；派生状态另给 `provisional_score_total`，不会回写或伪造单项评分。
- Challenge：counterargument（Statement）、channels_checked[]、search_scope、refutation_found、overturning_evidence、next_check。
- Conflict：description、source_ids[]、critical（bool）、resolved（bool）、resolution?。
- ResearchDraft：schema_version=1、ticker、as_of、answers[]、gates[]、moats[]、challenge、conflicts[]、conclusion（Statement）、review_explanations[]。
- ReviewExplanation：target_id（问题/门闸/维度或 conclusion）、statement（Statement）；没有 baseline 时列表为空。
- ValidationIssue：code、location、message、severity（error/warning）。
- ReviewIssue：category（question_alignment/evidence_support/numeric_consistency/cross_section_consistency/scope_violation）、target、severity、message、evidence[]、revision_instruction。
- DraftReview：draft_hash、status（passed/failed）、checked_questions、checked_gates、checked_moats、issues[]。不保存隐藏推理。

缺口允许完成，但不允许借缺口继续给未经证据支持的结论。score_total 和 quality_status 由程序派生，不接受模型输入。Draft 中不接受 valuation/target_price/action 字段。

## 3. SQLite 与文件

2026-10-08 公司数据层 A 已实现：独立 `data/company.db`（schema v1，所有公司共用）保存 source_batches、company_profile_facts、financial_facts；`wonder.db` 仍管理来源身份、研究与 Agent 状态，原始归档不迁移。公司服务只读主库，验证当前 ticker 目录、Source 身份及原始字节哈希，按来源/哈希/解析器版本幂等导入；失败批次不作为事实返回，未知数值/历史采集失败保留 partial 诊断。整批事务防半份事实，完整重建经临时库成功后才替换公司库。数值保留 Decimal 文本，来源未提供的期间/单位/披露元数据不推断。支持 profile、statements、operational_efficiency、revenue_breakdown。详见 [实际实现](28-company-data-store.md) 与 [后续计划](27-company-database-plan.md)。B 已接入显式 company_latest；旧范围的 QueryWorkspace 语义不变，详见 [共享查询实现](29-company-agent-query.md)。

公司数据层 B：新 chat 临时问题采用 company_latest，直接 ask 默认 latest_completed_report 不变。Turn.question_json 内保存受 Pydantic 校验的来源/解析器版本快照，重试不重选，追问继承 allow_network 并创建新范围。每类 Futu 数据选择最新获取来源，新 Turn 可复用他轮补取；原 run 范围不扩权。补取先归档/登记，再按所选 source_id 增量索引；未知、失败、partial 分开返回。query_company_data 默认返回字段与期间目录，facts 附查询导航；目录不能引用。公司 SQL 快照 v2 含基本字段、财务字段/期间类型/单位/会计口径/业务维度及来源/解析版本，旧范围仍用 v1。最终报告和 Memo 要求当前 Turn 实际回执及未变来源。单次输入目标从固定 5,000 改为基于规则/最新结果动态调整，最高 8,000；Turn 默认 30,000 总预算与六次决策不变，实际 usage 结算，既有预算不刷新。来源授权/数值计算可检查，但没有实现自然语言结论的确定性语义证明，真实答案单位臆断仍列为质量缺口。

CLI：`company-data sync [TICKER]`、`rebuild`、`status [TICKER]`、`fields TICKER`、`read TICKER`。人工查询支持 `--dataset/--field/--period/--source-id/--limit/--offset`，每表最多 200 行，参数绑定；选择语义为 all_indexed_sources，未去重为最新或历史时点事实。status 返回分组覆盖与有界异常诊断；无公司库时不自动建库。同步有 failed 时输出部分成功结果和非零退出码；partial 在结果/覆盖诊断中明确标识。

四张初始表：

- stocks(ticker PK, name nullable, watched bool, created_at)。研究不要求先加入清单；start 自动创建 watched=false 的股票。
- runs(id PK, ticker FK, kind, parent_run_id nullable FK, as_of, baseline_run_id nullable FK, status, draft_json nullable, schema_version, created_at, updated_at, completed_at nullable, error_json nullable, artifact_hashes_json nullable)。kind 首版为 standard；附记（addendum）与专题（topic）为研究规格第 9 节预留的类型，其正文与估值字段本版不写入。parent_run_id 指向附记挂靠的标准研究，baseline_run_id 仍只用于复评比较，两者语义不同。阶段 2 的 finish 有 baseline 时只保存关联，不生成 review.md；比较与 review.md 属于阶段 6。
- sources(id PK, ticker FK, kind, metadata_json, sha256, raw_path, text_path nullable, retrieved_at)。
- run_sources(run_id FK, source_id FK, PRIMARY KEY(run_id,source_id))。

阶段 4 已实现一张轻量运行步骤表 `run_steps`（schema v2，经 v1→v2 显式迁移加入），作为 `runs` 的执行明细，不是事件溯源或通用任务队列：

- `run_steps(run_id FK, step_key, attempt, status, input_hash nullable, output_ref nullable, provider nullable, model nullable, started_at nullable, finished_at nullable, error_json nullable, metadata_json nullable, PRIMARY KEY(run_id,step_key,attempt))`。阶段 7 已用显式 v2→v3 migration 增加 `metadata_json`，只保存模板版本、`usage`、`finish_reason` 等受控运行元数据，不含提示词、隐藏推理或认证头。
- `status` 只允许 `pending`、`running`、`succeeded`、`failed`、`blocked`、`skipped`；`attempt` 从 1 开始递增。
- `output_ref` 只指向已有的来源、草稿或受控 JSON 产物，不把大段原文或隐藏推理塞进步骤表。
- 采集器和未来 writer 使用它记录可重试边界、输入哈希、provider 错误和输出引用；`runs.draft_json` 仍是草稿的正式内容。`output_ref` 形如 `source:<uuid>`。
- 此表通过显式 schema migration（`_MIGRATIONS` v1→v2）加入，`initialize` 对已有 v1 库执行迁移，不重建已有表。

阶段 16 的 schema v6 增加 `draft_sections`（分组 input/output hash、JSON、provider/model/template）与 `draft_reviews`（draft hash、状态、reviewer 类型、模型、模板和结构化 review JSON）。新 standard run 需要 matching passing review；v5 历史 run 兼容原规则。阶段 16.1 的 schema v7 增加 FTS5 `source_page_fts`，按已归档非 Provider 来源保存 `source_id`、页/行 locator、内容和内容哈希，供确定性页级检索使用；它不保存提示词、隐藏推理或新抓取的数据。阶段 18 的 schema v8 增加 `agent_scratchpads`、FTS5 `memory_entry_fts`/`memory_entries`、`analysis_runs` 与 `source_conflicts`；v7 历史 completed run 不改变完成规则。

foreign_keys=ON、busy_timeout=5000；首版使用默认 journal，不先开 WAL。PRAGMA user_version 管理 schema，阶段 1–3 为 1、阶段 4–6 为 2、阶段 7 为 3、阶段 8–10 为 4、阶段 11–12 为 5、阶段 16 为 6、阶段 16.1 为 7；只添加显式递增迁移，不每次启动重建表。

答案保存在 runs.draft_json，SQL 不拆成 claim/evidence 图数据库。JSON 是 SQLite 的正式内容；导出 research.json 是同内容快照。原文文件是字节事实源。Markdown 为视图，手工修改不回写数据库。

文件布局：data/stocks/<ticker>/sources/<sha256>.<ext>，文本 <sha256>.txt；metadata 保留在数据库；相同字节允许复用文件，但不合并不同获取事件的 Source 记录。每个来源引用仍能追踪此次获取时间。

run 目录名由 created_at 的 UTC 秒级时间与完整 run_id 生成且固定。草稿不修改其他 run。所有数据库路径存相对项目根路径，导入文件复制到 data 后才可正式引用。

## 4. CLI 契约

共同选项：--root PATH。JSON stdout 为 {ok, data, issues}；issues 总为数组。正常/仅警告 exit 0，校验拒绝 exit 2，I/O或provider错误 exit 1；日志到 stderr，不打印秘密。JSON 输入 --input PATH，PATH=- 表示 stdin。

输出按 stdout 是否为 TTY 区分：交互终端中的 `finish` 以人类可读摘要展示完成状态、质量、严格证据总分与可用暂定总分、run 目录及 `company.md`、`research.json` 绝对路径；存在 `review.md` 时也列出。`chat` 完成正式研究时复用该摘要，不显示原始 JSON 或重复 session_id。stdout 被管道或重定向时，`finish` 保持 `{ok,data,issues}` JSON 契约。进度事件始终写 stderr。摘要遇到幂等 finish 返回值缺少质量/分数时，通过只读 `status` 查询补齐。

`company.md` 的展示规则：Q01–Q24 标题包含 `docs/research-spec.md` 的完整问题文本；答案状态、陈述类别、证据关系等面向读者的枚举转换为中文。数值使用千位分隔，保持 Decimal 原精度，不舍入或转换币种；相同的单位和币种只展示一次。引用按 source_id 的确定顺序使用短编号，文末索引映射回完整 source_id 和来源元数据。相同陈述内三元组（source_id、locator、relation）相同的引用只显示一次；数值引用若与所属陈述引用完全相同则不重复显示，不同 locator/关系仍保留。以上只改变 Markdown 呈现，不改变 ResearchDraft、校验、数据库或正式报告冻结规则；已完成报告不重写。

阶段 1 离线命令：

- validate-packet --input packet.json：packet={draft: ResearchDraft, sources: Source[]}；返回结构校验，不声称归档原文真实存在。
- render-packet --input packet.json --output PATH：先结构校验，再生成标注“离线样例/未正式归档”的报告；output 必须在 root/data/ 下。

阶段 2 起业务命令：

- watch add TICKER [--name NAME] / watch remove TICKER / watch list：只维护本地 watched，不删除历史。
- start TICKER [--as-of ISO] [--baseline RUN_ID] [--skill NAME]：默认 as_of 为当前带时区时间；产生 draft run 与 run_id，kind 固定为 standard。`--skill` 可重复指定可用的 `fixed_research` 方法/能力 Skill；Python 在创建 run 前按需加载正文与 reference，并仅记录名称、版本、哈希和能力。baseline 必须是同 ticker 的 completed run，时间不晚于新 run。不自动选未完成草稿。本版不提供附记/专题的启动开关，只保留表的 kind/parent_run_id 字段。
- skill-list / skill-check [NAME]：只读输出项目 Skill Catalog，或检查指定/全部 Skill 的 manifest、依赖、目录内 resources 和 Capability Registry 绑定；发现诊断返回 exit 2。
- status RUN_ID / history TICKER：返回状态、质量信息、来源摘要/历史元数据，不一次吐所有原文。
- draft-read RUN_ID / review-read RUN_ID：分别只读输出当前草稿和最新结构化审查 JSON；不调用模型、不生成报告、不改变 run。draft-preview RUN_ID：在 `data/tmp/draft-previews/<RUN_ID>/` 写入未定稿 Markdown 预览及已有的结构化审查预览，不改变 run。
- trace-read RUN_ID：只读输出该 run 的 Writer/Reviewer 本地调用树；没有模型调用时返回空 trace，不补造历史记录。
- save-draft RUN_ID --input packet.json：packet={draft: ResearchDraft, sources: Source[]}，沿用阶段 1 形状。保存已通过类型与身份校验的草稿，并把 packet 内来源登记到 sources/run_sources。阶段 5 起校验会把 packet 内来源与该 run 已挂来源合并（`_effective_sources`），因此 CLI/API 调用方引用 collect/source-add 已归档的来源时 `sources` 可留空，不会误报 `unknown_source`；真正未归档的引用仍被拒绝。允许研究内容缺失，返回业务校验 issues 但不阻止保存。
- validate RUN_ID：检查结构、答案状态语义、归档文件、时间、来源关联、schema v6 standard run 的当前语义审查，以及 schema v8 富途/官方未解决数值冲突；不写 completed。
- finish RUN_ID：校验后由服务渲染 company.md 和 research.json；schema v6 同时冻结 quality-review.json；有 baseline 时再渲染 review.md，全部产物哈希一并记录。旧 run 的产物不改写。
- resume RUN_ID：仅 failed→draft，保留原草稿和来源；completed 拒绝。普通校验失败仍保持 draft，不滥用 failed。

阶段 3–4：

- source-add RUN_ID --input source-request.json：request 含 metadata 及 file 或 url，二者恰好一个。程序取得原始字节、计算哈希和实际 retrieved_at，不能接受用户伪造哈希/获取时间。
- sources RUN_ID：列表返回元数据与 source_id；source-read RUN_ID SOURCE_ID [--page N | --start-line N] [--limit-lines N]：默认 120 行，上限 300 行与 20000 字符，返回 locator、截断标记和下一段位置。
- collect RUN_ID [--kind all|market|fundamentals|filings]：all 默认，包含三者。market 只归档报价快照；财务报表只由 fundamentals 的 `statements` 采集，避免重复请求。fundamentals 归档公司概况、高管、财报、主营构成、分红回购、股东和板块；标准研究不自动采集估值/评级。财报每类以 `num=50`/`next_key` 分页，保留最多 20 个去重年度期。每个数据集记 `dataset`/`evidence_class`/`available_from`/`currency`/`accounting_standard`/`observed_at`；filings 发现并归档最近年报/中期/季度报告（HKEX/SEC 公开渠道）。各任务在 `run_steps` 分列，失败/受限写 error_json 与 issues，不把 partial_success 当 complete coverage。采集后记录 `source_rank`/`extraction_method`，并用同 run 官方结构化明细核对富途数值。至少一个子任务成功时退出 0，全部 failed/blocked 时退出 1。富途基本面能力与只读边界见 [docs/08-futu-data-capabilities.md](08-futu-data-capabilities.md)。
- collect ... --quiet：关闭简短 stderr 进度；默认输出采集阶段的简短进度，stdout JSON 契约不变。

阶段 6（已实现）：

- compare RUN_ID：要求 baseline；Python 计算问题状态、门闸结果、护城河分数与总分的结构差异，并区分本次新增来源与沿用基准来源。未完成草稿可以预览；正式 review.md 随 finish 固化。任一侧分值为 null 时差值输出 null（不适用）；schema 或口径不同时 `comparable=false`，不算分差。
- backup --output PATH：目标限项目 data/backups/ 下的新目录，不覆盖已有备份。在项目写锁内用 SQLite backup API 生成 `wonder.db`，复制已归档来源与 completed run 产物到 `files/<相对路径>`，写 `manifest.json`（含每个文件与数据库的 SHA-256、missing 列表）和 `RESTORE.md`。锁被占用时报 `lock_timeout`（exit 1），业务写入不被静默并发。
  公司数据层 A 起，若 company.db 存在，同一写锁内另生成 company.db 快照，manifest/返回值增加可选 company_database（路径、哈希、schema/解析器版本）；恢复说明同时覆盖两库。无公司库时该项为 null，保持旧项目兼容。B 起按主库备份中的 analysis 状态复制 SQL 快照/查询/结果及已完成函数的代码/结果，旧 JSON 分析产物也收录；missing 和 incomplete_analyses 明示遗漏/未完成状态，路径越界、哈希变化报错。

阶段 7/16（已实现）：

- `write-draft RUN_ID --strategy staged|one-shot --provider deepseek [--model MODEL]`：默认 staged，分六组写 Answers，再生成综合判断并执行 DraftReview；输入哈希相同的成功分组直接复用。分组 Prompt、JSON Schema、解析、缓存和修订统一使用顶层 `AnswerSection={answers:[...]}`；损坏外层 JSON 不扫描内部 Statement/Gap 冒充 Answer。一次尝试总调用硬上限 15，语义审查 error 与可映射到草稿单元的 Python 硬校验 error 合并后，最多修订 5 个单元且每单元一次，最终审查或校验仍失败则保持 draft。one-shot 仅用于回归，但不能绕过新 run 的审查门闸。
- `review-draft RUN_ID [--provider deepseek | --input review.json]`：重复模型审查或导入人工审查；两者都必须通过 hash、覆盖和引用边界校验。
- Writer prompt 由 `questions.py` 提供 Q01–Q24 完整问题文本，与正式报告标题共用，当前模板版本为 `deepseek-draft-v3`。Q03 要求资本开支、收入比例、期间和口径证据，不接受利润率、ROE、净利润或 EPS 替代；Q04 要求技术替代/产品变化速度及竞争影响，不接受 EPS、利润率或估值指标替代。提示要求证据不足时降级，但模型提示本身不是语义证明；程序 `validate` 仍只校验结构、引用关系、来源资格和时间边界，不能保证自然语言语义正确。
- 固定研究的 `chat` 若 collect/Writer/存储失败，返回 `status=failed`、`run_id`、`failed_step` 和逐项 `issues`，并保存 `resume_fixed_research`；同一 session 输入“重试”复用原 run、来源和成功分组，不重复 collect。`validate` 失败返回 `status=validation_failed`。两类失败都不生成正式产物，交互 TTY 显示错误位置/代码/说明；非 TTY 返回 `ok=false` JSON 并以 exit 2 结束。程序不会把 context 引用自动升级为 supports，也不会自动 finish。finish 写入或校验失败同样保留 session 与原 draft，允许修正后重试。
- write-draft、ask 和 ask-followup 支持 `--quiet`；默认只输出简短 stderr 进度。

所有命令在模型传值前有 --help，输入错误给出字段路径和修正说明。工具能力只允许文档中列出的操作，不提供任意 Python/SQL 执行命令。

## 11. 阶段 11–12：进度事件与独立自然语言入口

`ProgressEvent` 是宿主无关的最小事件：

```json
{
  "type": "progress",
  "phase": "collect.fundamentals",
  "status": "succeeded",
  "message": "基本面数据采集完成",
  "run_id": "...",
  "session_id": null,
  "timestamp": "..."
}
```

默认由 CLI 的 `StderrProgress` 输出人类可读的一行；stdout 始终保留 `{ok,data,issues}` 最终结果。ProgressEvent 不包含隐藏推理、完整原文、密钥或完整工具结果。

`skill_registry.py` 先扫描项目根 `skills/*/SKILL.md` 和可选 `skill.json`，生成含 name、description、path、kind、version、applies_to、availability 与 content hash 的 Catalog。Router 只接收这份摘要，不能接收正文或 reference。`RoutePlan` 另有默认空数组的 `skill_names` 字段；旧 Router 输出继续兼容。

无 `skill.json` 的目录是纯方法 Skill。能力 Skill 必须在 manifest 声明 `kind=capability`、至少一个 capabilities，并只引用 `capabilities.py` 中已经注册且可用的能力。可选 `tools` 只能引用 `tool_registry.py` 中有 Pydantic 参数 schema、适用 capability 和已绑定 Python handler 的稳定工具 ID；纯方法 Skill 不能声明工具。所有 prompt_resources 必须位于 Skill 目录内。实际执行才用 `load_for_capability()` 读取正文和 reference；重名、缺 description、路径越界、依赖循环、未知/不可用 capability、重复选择、未注册/未授权工具或能力不匹配都拒绝。

固定研究把实际加载的摘要写进 `run_steps.skill_selection`，随后 Writer 按摘要重新加载内容并纳入输入哈希；交互 Agent 在 `chat` 执行边界合并 capability 基础工具与所选 Skill grants，只把获准工具 schema 放入 Agent prompt，并在 handler 调用前用同一 allowlist 校验。session event 记录 Skill 摘要、授权工具 ID 和 registry version；续问重新加载并核对哈希与权限。直接 `ask` 为既有调用兼容保留原工具集。审计不保存 Skill 正文、reference、提示全文或隐藏推理。Fixed Research Writer 不使用该动态工具路由。详见 `docs/14-skill-on-demand.md` 与 `docs/17-skill-driven-tool-routing.md`。

`router.py` 调用一次 DeepSeek 并返回严格的 `RoutePlan`。支持 `fixed_research`、`interactive_question`、`memo_save`、`session_followup`、`status_query`、`clarify` 和 `unsupported`。Router 不直接执行数据库、网络或文件操作。

自然语言入口：

```text
chat
chat --text TEXT [--session-id SESSION_ID] [--model MODEL] [--quiet]
```

`--text` 在真实终端中是会话的首轮输入：若结果处于 `awaiting_confirmation`、`awaiting_finish` 或 `waiting_user`，CLI 会继续读取 stdin，复用同一个 `chat_session`。当 stdin 非交互（管道/重定向）时保持一次性 JSON 输出，不等待后续输入。该会话循环参考 Dexter 的 Agent loop，但不改变 Python 服务对工具、证据和正式报告状态的控制。

当 Router 返回 `ticker=null` 时，chat 进入 `awaiting_symbol_selection`，把候选保存在 `chat_sessions.pending_json`。候选来自本地清单、富途 `get_search_quote` 或标记为 `verified=false` 的模型建议；候选搜索不写入 `sources`，不创建 `ResearchRun`，用户必须选择并再次确认后才能启动正式研究。

正式研究的自然语言路径为：

```text
RoutePlan
→ 用户确认
→ start
→ collect --kind all
→ write-draft
→ validate
→ 用户确认
→ finish
```

用户可以使用不严格的自然语言；公司或意图不明确时必须澄清。schema v5 的 `chat_sessions` 保存待确认计划、历史、ticker、run_id 和交互 Agent session 关联，支持终端重启后恢复。

当前只有一个完整交互 Agent。Router 是一次分类调用，正式报告 Writer 是一次结构化写作调用。宏观、产能、库存周期和同行业专用 Agent 不属于阶段 11–12；TypeScript runtime 和 LangGraph 也不属于当前实现依赖。

## 5. 核心服务边界与状态

阶段 1 必须有 validate_packet(packet)->list[ValidationIssue] 与 render_company(packet)->str；阶段 2 业务统一经 research.py 接入 storage。CLI 只做参数解析、调用、序列化，不藏业务规则。

finish 的顺序：取得项目写锁 → 读取并校验 run → 原子替换该未完成 run 的临时产物 → 事务写入 completed 与产物哈希。崩溃在文件生成与数据库提交之间时仍未完成，重试可安全重建；completed 重试只检查并返回原产物，哈希不符报 artifact_mismatch，不擅自覆盖。

所有业务写操作使用项目内标准库锁文件机制（macOS/Linux 用 fcntl，非目标 Windows 本版明确不支持）并有 5 秒获取超时；不持锁进行网络下载，下载完成后再锁定登记。下载暂存于 data/tmp，最终关联前重新检查 run 可写。

provider 错误记录来源收集 issue，可继续其他资料。未处理的持久化/渲染错误将未完成 run 标 failed（数据库自身不可用时仅 stderr 明确记录，不能声称状态成功保存）。resume 不重放此前网络请求。

## 6. 网络、时间与财务默认值

requests 连接超时 10 秒、读取超时 90 秒；网络或 429/5xx 最多 2 次尝试，尊重 Retry-After 并限制等待到 60 秒，超出则可重试错误；其余 4xx 不自动重试。单文件上限 50 MiB，流式下载检查实际字节；只接受 HTTP(S)，限制重定向 5 次，禁止从公开 URL 跳到回环/私网地址。OpenD 是 market.py 单独配置的本机连接，不经 URL 下载器。

不执行下载的脚本、宏或 HTML；提取失败保留原文并明确标状态。PDF 扫描件提示人工补文本，不自动安装 OCR。

公告发现默认回看 5 年，HKEX 候选上限 200，自动选取最近 5 份年报与最近 3 份中期/季度报告（以官方渠道实际可取得内容为准）。HKEX 用官方 `prefix.do`/`titleSearchServlet.do`，检索 `rowRange` 放宽后过滤相关类别；SEC EDGAR 需环境变量 `WONDER_SEC_USER_AGENT`（SEC 要求带联系方式），未设置时该通道标 blocked，不影响其他能力。财务结构化接口每类请求最多取 10 个年报期间；不把记录数直接等同于连续完整财年，必须核对每条 `period_end`。

阶段 13 数据基线：针对中国移动、中国联通、中国电信，目标归档 FY2021–FY2025 年报和最近 3 份定期中期/季度报告；富途财务保留最多 10 个年度期间，主营构成尽可能保留最多 10 个年度期间及最近 3 个中报期间。报告期间、披露时间、币种、会计准则、端点和原始字段均须保留；不可取得项应记录为缺项或通道阻塞，不得推断为零或不存在。

provider 来源应另外保存 observed_at 或原始披露的时间依据：retrieved_at 是取得时间，不能当作历史数值的发布时间。start 固定 as_of 后取得的新报价，若 observed_at 晚于 as_of，只保留为本次下载结果，不关联为该 run 的有效证据；公司研究可继续，不能悄悄前移截止时点。财务快照如能通过已归档的一手披露核对发布时间和具体数值，可引用该披露；不能仅因报告期早于 as_of 就认定晚取得的修订数据当时已可用。无法确定时间的 provider 数值保留为缺口/背景。

阶段 7.1 起（研究规格第 6 节“方案 4”）：provider 数据集保存 `available_from`（报告期末/披露日）。有 `available_from` 且不晚于 `as_of` 的已报告数据（财报、分红、主营构成、股东、高管）可支撑核心结论；无 `available_from` 的实时快照（行情、估值、分析师评级）仍按 observed_at 规则，仅作 context。

富途财务请求记录数不等于完整财年数。期间连接必须包含 ticker、period_end、period_type、currency、accounting_standard；原值和规范化值分开，不猜单位。本版优先展示原始可靠指标；遇到金融企业不硬套工业企业 ratios。时间可用性按研究规格。

## 7. 测试与交付

测试至少覆盖模型约束、缺分值、来源关联与未来资料、持久化恢复、不可覆盖、失败重试、报告导出与基准比较。fixture 无真实账户/秘密，网络函数被 fake 替换。富途 SDK lazy import；不依赖真实 OpenD 的 CI。

第 1 阶段实际建立后应能运行 uv run pytest 和 uv run ruff check .；把确实运行的结果写入 PROGRESS，不预先填写通过。每阶段文档需使用真实符号和已运行命令。

备份验证（阶段 6 已实现）：用 SQLite backup API、一份 sources/ 与 completed run 文件清单及 SHA-256 manifest；恢复演练创建临时根，复制 `wonder.db` 与 `files/`，验证历史可读、原文 `source-read` 可读、completed 产物哈希（含 review.md）一致。暂不实现云同步、自动定时备份或事件重放。

## 8. 阶段 7：直接调用 DeepSeek 写报告（已实现）

数值审查补充（2026-10-07）：模型审查之后由 Python 核对可识别的完整 ROE 阈值计数、归档 ROE 精度和已标明公式的债务权益比四舍五入。只在数值、期间、字段与来源定位可核对时降级明确误报；浮点导入误差容差为绝对值 `1e-8`，不容忍实质数值改动。`review-draft --recheck` 可离线重检既有同 hash 模型审查并追加记录，模板标注 `+offline-numeric-v1`，不会伪称新 API 调用、覆盖旧审查、改草稿或执行 finish。人工及过期审查拒绝复用；其他语义 error 继续阻塞。

用户已确认本版本在阶段 6 后接入 DeepSeek API。实现保持研究模型、证据、存储、校验和渲染独立于宿主，只新增一个小型 `writer.py`，不引入 SDK 依赖或 provider 注册框架。

writer 从现有 run 的已归档来源与带 source_id/locator 的原文片段（分段截取，总长受控）构造上下文，模型返回 ResearchDraft，再调用现有 save-draft/validate 保存与验证。写作与数据采集分开；仅给已有材料写作不需要完整 Agent 循环。若将来需模型自己搜索和调用工具，再单独设计该执行过程。真实 API smoke 尚未运行（本环境无 `DEEPSEEK_API_KEY`）。

- 输入：run_id、明确选择的模型配置，以及从本地规则和归档材料构造的受限上下文；不依赖读取 OpenCode 私有会话数据库。
- 输出：满足既有 schema 的 ResearchDraft；模型不能新造 source_id、locator、来源日期或跳过门闸/反证要求，评分总分仍由 Python 计算。
- 单供应商起步，阶段 7 固定为 DeepSeek；不因 API 格式兼容而假定结构化输出、参数或模型能力，实际接入时依官方文档和测试确认。
- 设置明确超时、输出限制和调用预算；不合格 JSON/schema/引用返回错误与草稿，不无限自动重试、不因修复失败强行 finish。
- 用户在运行环境自行设置凭据。程序可读取环境变量用于请求，但不得持久化或输出密钥/请求认证头；对话中不得索取秘密。阶段 7 起 `envfile.py` 会加载项目根 `.env`（键值解析、不覆盖已有环境变量、不打印），`.env` 已在 `.gitignore` 中、不入仓库；仓库只保留无密钥的 `.env.example` 模板。
- 在 `run_steps` 的阶段 7 `metadata_json` 记录供应商、模型、输入哈希、提示模板版本、受控输出引用及可取得的用量摘要；禁止保存认证头、完整提示词、隐藏推理或敏感认证内容。
- 测试使用 fake LLM 返回正常草稿、非法 JSON、虚假引用和超时，确认失败不生成 completed 报告；真实 API smoke 在用户要求该阶段且运行环境准备好后进行。

直接接模型不会自动赋予浏览、PDF 提取、网络搜索和数据库写入能力；这些仍由现有 Python 工具与显式流程提供。是否加入自主工具循环需独立决策。

核对入口：[DeepSeek 官方 API 文档](https://api-docs.deepseek.com/zh-cn/)。本次只确认存在 API 接入途径，未实际调用或验证任何特定模型效果。

## 9. 编排与宿主边界（2026-09-20 决策）

### 9.1 默认路径

- 领域逻辑继续使用普通 Python 函数、小模块和显式事务。
- CLI 是项目自身稳定的运行接口；固定研究、自然语言入口、Skill Registry、Agent session、DeepSeek 调用都可在本项目 Python 运行时内完成，不要求外部宿主。用户自建 Agent 可选调用 CLI/API，但不是事实来源。
- 研究必须能从 `run_id`、SQLite、来源文件和正式产物重建；不能只依赖外部聊天会话。
- 只有在已归档材料上生成结构化 `ResearchDraft` 时，优先使用小型 writer；不为了单次模型调用引入图编排。

阶段 5 的 OpenCode 配置是历史兼容产物。当前 Skill 由项目内 `skill_registry.py` 读取；后续实现不得以 OpenCode 安装、会话或私有数据为前提。

### 9.2 LangGraph 的启用条件

LangGraph 只能作为阶段 7 完成之后的可选 orchestration 包，启用前必须有真实运行证据表明至少存在两项问题：动态工具选择、多轮补证据、跨天恢复、独立问题并行，或人工监督流程难以审计。它的节点只能调用现有业务服务；不能直接写 SQLite、修改来源元数据、计算护城河总分或绕过 `validate`/`finish`。

LangGraph 状态只保存 `run_id`、步骤键、结构化中间结果和输出引用；SQLite/文件系统仍是事实来源。若实际需求只是单次结构化写作，继续用 writer；不要以 LangGraph 替代现有 `runs` 和来源归档。

### 9.3 暂不采用的编排系统

Temporal、Prefect 和多 Agent 框架暂不加入首版。只有出现后台调度、远程 worker、多用户并发或跨基础设施长期运行需求时，才重新评估基础设施级 durable execution；这属于产品范围变化，需要单独决策和验收。

## 10. 阶段 8–10：独立交互 Agent 与 Memo

### 10.1 产物边界

固定研究流程与交互 Agent 是两个并行入口：

```text
固定 Prompt/CLI → ResearchRun → ResearchDraft → validate → finish → 正式报告
开放问题       → AgentSession → InteractiveReport → memo-save → CompanyMemo（可选）
```

`ResearchRun` 是标准公司研究的权威版本；`InteractiveReport` 是一次临时回答；`CompanyMemo` 是用户明确保存的公司观察。交互 Agent 不自动修改 `runs.draft_json`、评分、门闸、来源元数据或完成产物。

### 10.2 新数据表

schema v4 增加：

- `agent_sessions`：session、ticker、可选 run_id、问题 JSON、状态、provider/model、迭代和 token 预算、结果引用与错误。
- `agent_events`：有限调用轨迹；只保存工具名、参数、结果摘要和时间，不保存隐藏推理、认证头或 API Key。
- `interactive_reports`：`InteractiveReport` JSON；不参与公司评分。
- `company_memos`：公司 Memo、来源引用、创建 session、标签、标题版本和时间。

同一 ticker 和 title 的 Memo 再次保存时递增 `version`，不覆盖旧记录。

schema v10 增加 `agent_turns` 与 `agent_evidence_receipts`，并为 `analysis_runs` 增加可空的 `turn_id`、`sql_hash`。`AgentTurn` 固化每次 `ask`/`ask-followup` 的问题、ticker/run、Skill/allowlist、provider/model 和预算快照；它不是 19D 的 Attempt。v9→v10 迁移会给历史 session 写入 `legacy-<session_id>` Turn，并把既有分析记录关联过去，不改写事件、报告、Memo 或正式研究。

### 10.3 CLI 契约

```text
ask TICKER --input InteractiveQuestion.json [--model MODEL]
ask-followup SESSION_ID --input InteractiveFollowup.json
ask-status SESSION_ID
agent-trace SESSION_ID
memo-save SESSION_ID --input MemoRequest.json
memo-list TICKER
memo-read MEMO_ID
```

`ask`、`ask-followup` 和 `ask-status` 只读或写 Agent 会话结果；`agent-trace` 只读输出由 session 关联的模型与工具 span；`memo-save` 是唯一把交互结果保存为公司长期 Memo 的入口。当前版本不提供 agent-promote，也不把临时结果自动转换成 `ResearchDraft`。`InteractiveQuestion.focus` 支持 `general`、`challenge`、`macro`、`peer`、`capacity_cycle` 和 `inventory_cycle`，角色共享同一工具边界。

### 10.4 运行时 trace

schema v9 增加 `traces` 和 `trace_spans`。前者关联可选的 `run_id` 或 `session_id`，后者以
parent span 形成调用树；状态为 `running`、`succeeded` 或 `failed`。`trace-read RUN_ID` 和
`agent-trace SESSION_ID` 只读输出已保存的树。trace 默认只存模型/调用种类、token、长度、哈希、
工具名、耗时和受控错误码；不得保存 prompt、模型完整输出、工具完整结果、隐藏推理、认证头或
密钥。

设置 `WONDER_TRACE_EXPORTER=otlp` 后，安全属性通过可选 OpenTelemetry OTLP/HTTP exporter 发出；
没有 optional 依赖或 collector 不可用不改变核心业务结果。仅当用户在执行进程中显式设置
`WONDER_TRACE_CAPTURE_CONTENT=preview|full` 时，exporter 才向该 OTLP collector 额外发送可见的
LLM input/output 与 Agent tool input/output；`preview` 默认每段最多 12,000 字符，`full` 不截断。
上述正文绝不写入 SQLite 或 CLI trace；API key/Authorization 与模型隐藏推理字段始终不采集。
设置 `WONDER_TRACE_EXPORTER=langfuse` 时，同一 exporter 以 Langfuse OTLP/HTTP 规范发送，凭据只从
`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`（或等价的 `WONDER_LANGFUSE_*`）读取；自动配置 Basic
Auth、`x-langfuse-ingestion-version: 4`、Langfuse observation type、trace name 和 Agent session grouping。
不得将任一 Langfuse key 持久化、打印或写入本地 trace。

### 10.5 Agent 工具和模型协议

阶段 18 在原有 `get_run_status`、`list_sources`、`read_source`、`search_archived_sources`、`read_report`、`list_memos`、`read_memo` 上新增 `query_archived_financials`、`run_trusted_calculation`、`create_analysis_dataset`、`run_analysis_code`、`read_analysis_result`。工具参数使用 Pydantic，工具结果有字符上限，循环有最大迭代次数和 token 预算。模型通过 JSON 决策协议选择 `tool_call`、`final` 或 `ask_user`；DeepSeek 由 Python 直接调用，不需要 OpenCode、MCP 或 LangGraph。

2026-10-08 按用户最新指令新增 `fetch_financial_data`：缺少原始数值时补取当前公司的富途只读财务资料（statements、operational_efficiency、revenue_breakdown）。新问题默认 `allow_network=true`，显式 false 在打开连接前拒绝；不提供任意 API、网络、账户或交易权限。补取来源独立归档到 `data/stocks/<ticker>/agent-sources/<session>/<turn>/`，不写 `run_sources`；引用范围限当前 Turn，显式 Memo 可保存来源。新 SQL 包含补取资料，旧快照不变。默认总 token 预算为 30,000，历史 Turn 不调整；请求容量与累计消耗分别处理。固定计算器是快捷路径，其他指标由模型选择 SQL/受限函数。函数支持同步推导式、循环和 +=，继续禁止 import、属性访问、动态执行、shell、网络与任意文件访问；保留十秒、256 MB、64 KB 限制。旧数据集工具保留兼容。设计、真实验证和限制见[通用财务问答](25-agent-context-and-financial-tools.md)。

阶段 19C 已为新版 `analysis-workspace` Skill 实现 QueryWorkspace：对当前 session/turn/ticker/run 导出的结构化财务事实 SQLite 快照只接受单条只读 `SELECT` 或 `WITH … SELECT`，再将结果交给受限 `def analyze(rows)` 函数。快照、SQL、函数和完整结果保存在 `data/tmp/analysis/<session>/<turn>/<analysis>/`；主数据库永不接受 Agent SQL。SQL 在独立的受限子进程中通过 `immutable` 只读连接、SQLite authorizer、`query_only`、progress handler 和 10 秒/256 MB/64 KB 限制执行，拒绝写操作、DDL、PRAGMA、ATTACH/DETACH、事务、多语句和扩展加载。最终交互报告的引用必须匹配同一 Turn、同一来源哈希的实际工具证据回执。详情见 [docs/21-query-workspace.md](21-query-workspace.md)。旧 JSON 数据集工具仍只服务兼容入口。

### 10.6 独立运行要求

用户只提供 `DEEPSEEK_API_KEY`、可选的 `WONDER_DEEPSEEK_MODEL`，以及已归档资料或富途/OpenD 配置，就可以直接运行项目内 Agent CLI。无需 OpenCode 或其他外部宿主；SQLite、`data/`、session、Scratchpad、Memory 和 Memo 是独立运行后的事实来源。`memory-search TICKER [--query TEXT]` 与 `memory-read MEMORY_ID` 是只读检查入口。

实现交互 Agent 时，参考用户指定的 [Dexter](https://github.com/virattt/dexter) 的 Agent loop、tool registry、skills registry 和 scratchpad，但不复制其 Bun/TypeScript 技术栈，也不复制开放网络、股票筛选、估值/目标价或交易行为。


### 19D.1 执行与恢复契约（2026-10-07）

schema v11 增加 AgentAttempt、event/trace 的 Turn/Attempt 标识、按 Turn 不可变临时报告以及 Memo 来源 Turn；v12 显式回填无 Attempt 的 legacy Turn 状态/预算和旧 event/trace 的 Turn 关联。历史报告正文、ResearchRun 和冻结产物不改写。当前代码版本为 v12；本阶段只迁移副本，主库仍为 v11。

`ask-followup` 创建新问题 Turn；`ask-respond SESSION_ID TURN_ID --input FILE` 仅向 waiting_user 补充；`ask-retry SESSION_ID TURN_ID` 仅恢复 failed/interrupted。两者都新建 Attempt，保留原目标、授权、用户补充和累计预算。`ask-status` 返回 Turn/Attempt 历史。成功报告不能覆盖。`ask-interrupt SESSION_ID TURN_ID` 仅结算无存活持锁进程的遗留 running Attempt，不自动重放工具。

Attempt 创建、预算进度、最终状态/报告/episode/FTS、Memo/记忆投影分别使用显式短事务。Session 执行锁贯穿循环，项目写锁只保护短写入，网络调用不持有项目写锁。Ctrl+C 和普通执行异常按已持久化进度结算；硬崩溃由显式 interrupt 恢复。每次模型调用先预留剩余 Turn 预算，已知 usage 后按实际消耗更新；未知 usage 保留预留，不能按免费调用重试，此时 used_tokens 是保守占用而非真实账单。API max_tokens 按剩余预算减去保守输入估算限制，不承诺精确费用上限。同 Attempt 重复调用拒绝，后续 Attempt 仅在同 Turn 复用有完整结果或受控分析产物的成功调用。

模型适配层只服务交互 Agent，生产仍使用 DeepSeek。正式 Writer 当前 MAX_MODEL_CALLS=15，规格同步现有实现与回归测试；此次未修改 Writer 调用次数常量。详情和实际验证见 [19D.1 中文讲解](23-agent-runtime-reliability.md)。


2026-10-08 修正：Agent 输入估算统一由 `agent_memory.estimate_input_tokens` 执行（ASCII 0.4、非 ASCII 1.0、每消息 32），不把字符数直接当 token；这是启发式、不是精确 tokenizer。压缩同时受字符阈值和剩余 Turn 预算减输出预留触发，保留规则/工具 schema 与完整问题。首轮记忆最多两条、每条 1,200 字符与三个来源索引；来源目录省略 metadata，原数据仍保留。真实累计计数仍采用 usage。见 [首轮预算修复](24-agent-context-budget-fix.md)。
