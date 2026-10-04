# 技术规格 v1

本文件给出本版本实施默认值，供新会话直接实现。边界来源于 PLAN；字段、CLI 名称、超时等为工程默认值，可因实际接口验证做不改变产品语义的小幅修正并记录。阶段 7 的直接 DeepSeek 写作边界见第 8 节，阶段 8–10 的独立交互 Agent 边界见第 10 节，LangGraph 编排演进边界见第 9 节。

## 1. 模块与依赖

包 wonder_stockresearch，入口 wonder-research = wonder_stockresearch.cli:main，兼容 python -m wonder_stockresearch。

按职责逐阶段建立：models.py（契约）、research.py（服务/校验）、reports.py（纯渲染）、storage.py（SQLite/文件）、sources.py（获取提取）、market.py（富途行情/财务）、futu_fundamentals.py（富途基本面变量）、disclosures.py（公告）、writer.py（阶段 7 DeepSeek 写作）、agent.py（阶段 8–10 交互 Agent 与 Memo）、capabilities.py（受控 capability 定义）、skill_registry.py（项目 Skill Catalog 与按需加载）、router.py（自然语言 RoutePlan）、chat.py（自然语言会话和确认）、progress.py（进度事件）、envfile.py（加载项目根 .env）、cli.py/__main__.py。不先生成空模块或多层接口。

Python >=3.11；Pydantic >=2,<3。sqlite3/argparse/json/hashlib/decimal/pathlib 为标准库。开发用 pytest、ruff；HTTP 用 requests，文字 PDF 用 pypdf。富途 SDK 阶段 4 锁定 `futu-api==10.11.7108`（实测可连接本机 OpenD 并返回快照/财务）。uv.lock 随实现维护。不在文档里承诺未经验证的 SDK 版本。

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
- Moat：dimension_id、score（int 0..10 或 null）、confidence（low/medium/high）、rationale（Statement）、gap?。
- Challenge：counterargument（Statement）、channels_checked[]、search_scope、refutation_found、overturning_evidence、next_check。
- Conflict：description、source_ids[]、critical（bool）、resolved（bool）、resolution?。
- ResearchDraft：schema_version=1、ticker、as_of、answers[]、gates[]、moats[]、challenge、conflicts[]、conclusion（Statement）、review_explanations[]。
- ReviewExplanation：target_id（问题/门闸/维度或 conclusion）、statement（Statement）；没有 baseline 时列表为空。
- ValidationIssue：code、location、message、severity（error/warning）。
- ReviewIssue：category（question_alignment/evidence_support/numeric_consistency/cross_section_consistency/scope_violation）、target、severity、message、evidence[]、revision_instruction。
- DraftReview：draft_hash、status（passed/failed）、checked_questions、checked_gates、checked_moats、issues[]。不保存隐藏推理。

缺口允许完成，但不允许借缺口继续给未经证据支持的结论。score_total 和 quality_status 由程序派生，不接受模型输入。Draft 中不接受 valuation/target_price/action 字段。

## 3. SQLite 与文件

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

阶段 16 的 schema v6 增加 `draft_sections`（分组 input/output hash、JSON、provider/model/template）与 `draft_reviews`（draft hash、状态、reviewer 类型、模型、模板和结构化 review JSON）。新 standard run 需要 matching passing review；v5 历史 run 兼容原规则。阶段 16.1 的 schema v7 增加 FTS5 `source_page_fts`，按已归档非 Provider 来源保存 `source_id`、页/行 locator、内容和内容哈希，供确定性页级检索使用；它不保存提示词、隐藏推理或新抓取的数据。

foreign_keys=ON、busy_timeout=5000；首版使用默认 journal，不先开 WAL。PRAGMA user_version 管理 schema，阶段 1–3 为 1、阶段 4–6 为 2、阶段 7 为 3、阶段 8–10 为 4、阶段 11–12 为 5、阶段 16 为 6、阶段 16.1 为 7；只添加显式递增迁移，不每次启动重建表。

答案保存在 runs.draft_json，SQL 不拆成 claim/evidence 图数据库。JSON 是 SQLite 的正式内容；导出 research.json 是同内容快照。原文文件是字节事实源。Markdown 为视图，手工修改不回写数据库。

文件布局：data/stocks/<ticker>/sources/<sha256>.<ext>，文本 <sha256>.txt；metadata 保留在数据库；相同字节允许复用文件，但不合并不同获取事件的 Source 记录。每个来源引用仍能追踪此次获取时间。

run 目录名由 created_at 的 UTC 秒级时间与完整 run_id 生成且固定。草稿不修改其他 run。所有数据库路径存相对项目根路径，导入文件复制到 data 后才可正式引用。

## 4. CLI 契约

共同选项：--root PATH。JSON stdout 为 {ok, data, issues}；issues 总为数组。正常/仅警告 exit 0，校验拒绝 exit 2，I/O或provider错误 exit 1；日志到 stderr，不打印秘密。JSON 输入 --input PATH，PATH=- 表示 stdin。

输出按 stdout 是否为 TTY 区分：交互终端中的 `finish` 以人类可读摘要展示完成状态、质量、护城河总分、run 目录及 `company.md`、`research.json` 绝对路径；存在 `review.md` 时也列出。`chat` 完成正式研究时复用该摘要，不显示原始 JSON 或重复 session_id。stdout 被管道或重定向时，`finish` 保持 `{ok,data,issues}` JSON 契约。进度事件始终写 stderr。摘要遇到幂等 finish 返回值缺少质量/分数时，通过只读 `status` 查询补齐。

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
- save-draft RUN_ID --input packet.json：packet={draft: ResearchDraft, sources: Source[]}，沿用阶段 1 形状。保存已通过类型与身份校验的草稿，并把 packet 内来源登记到 sources/run_sources。阶段 5 起校验会把 packet 内来源与该 run 已挂来源合并（`_effective_sources`），因此 CLI/API 调用方引用 collect/source-add 已归档的来源时 `sources` 可留空，不会误报 `unknown_source`；真正未归档的引用仍被拒绝。允许研究内容缺失，返回业务校验 issues 但不阻止保存。
- validate RUN_ID：检查结构、答案状态语义、归档文件、时间、来源关联，以及 schema v6 standard run 的当前语义审查，不写 completed。
- finish RUN_ID：校验后由服务渲染 company.md 和 research.json；schema v6 同时冻结 quality-review.json；有 baseline 时再渲染 review.md，全部产物哈希一并记录。旧 run 的产物不改写。
- resume RUN_ID：仅 failed→draft，保留原草稿和来源；completed 拒绝。普通校验失败仍保持 draft，不滥用 failed。

阶段 3–4：

- source-add RUN_ID --input source-request.json：request 含 metadata 及 file 或 url，二者恰好一个。程序取得原始字节、计算哈希和实际 retrieved_at，不能接受用户伪造哈希/获取时间。
- sources RUN_ID：列表返回元数据与 source_id；source-read RUN_ID SOURCE_ID [--page N | --start-line N] [--limit-lines N]：默认 120 行，上限 300 行与 20000 字符，返回 locator、截断标记和下一段位置。
- collect RUN_ID [--kind all|market|fundamentals|filings]：all 默认，包含三者。market 含报价快照与财务原始数据（富途只读）；fundamentals 归档富途基本面变量（公司概况、高管、财报、主营构成、分红回购、股东、板块、估值/评级），每个数据集记 `dataset`/`evidence_class`/`available_from`/`currency`/`accounting_standard`/`observed_at`；filings 发现并归档最近年报/中期/季度报告（HKEX/SEC 公开渠道）。各任务在 `run_steps` 分列，失败/受限写 error_json 与 issues，不把 partial_success 当 complete coverage。只做数据收集，不生成评分、不 finish。至少一个子任务成功时退出 0，全部 failed/blocked 时退出 1。富途基本面能力与只读边界见 [docs/08-futu-data-capabilities.md](08-futu-data-capabilities.md)。
- collect ... --quiet：关闭简短 stderr 进度；默认输出采集阶段的简短进度，stdout JSON 契约不变。

阶段 6（已实现）：

- compare RUN_ID：要求 baseline；Python 计算问题状态、门闸结果、护城河分数与总分的结构差异，并区分本次新增来源与沿用基准来源。未完成草稿可以预览；正式 review.md 随 finish 固化。任一侧分值为 null 时差值输出 null（不适用）；schema 或口径不同时 `comparable=false`，不算分差。
- backup --output PATH：目标限项目 data/backups/ 下的新目录，不覆盖已有备份。在项目写锁内用 SQLite backup API 生成 `wonder.db`，复制已归档来源与 completed run 产物到 `files/<相对路径>`，写 `manifest.json`（含每个文件与数据库的 SHA-256、missing 列表）和 `RESTORE.md`。锁被占用时报 `lock_timeout`（exit 1），业务写入不被静默并发。

阶段 7/16（已实现）：

- `write-draft RUN_ID --strategy staged|one-shot --provider deepseek [--model MODEL]`：默认 staged，分六组写 Answers，再生成综合判断并执行 DraftReview；输入哈希相同的成功分组直接复用。一次尝试总调用硬上限 14，最多修订 5 个单元且每单元一次，最终审查仍失败则保持 draft。one-shot 仅用于回归，但不能绕过新 run 的审查门闸。
- `review-draft RUN_ID [--provider deepseek | --input review.json]`：重复模型审查或导入人工审查；两者都必须通过 hash、覆盖和引用边界校验。
- Writer prompt 由 `questions.py` 提供 Q01–Q24 完整问题文本，与正式报告标题共用，当前模板版本为 `deepseek-draft-v3`。Q03 要求资本开支、收入比例、期间和口径证据，不接受利润率、ROE、净利润或 EPS 替代；Q04 要求技术替代/产品变化速度及竞争影响，不接受 EPS、利润率或估值指标替代。提示要求证据不足时降级，但模型提示本身不是语义证明；程序 `validate` 仍只校验结构、引用关系、来源资格和时间边界，不能保证自然语言语义正确。
- 固定研究的 `chat` 若 `validate` 失败，返回 `status=validation_failed`、`run_id` 和逐项 `issues`，run 保持 draft 且不生成正式产物。交互 TTY 显示错误位置/代码/说明；非 TTY 返回 `ok=false` JSON 并以 exit 2 结束。程序不会把 context 引用自动升级为 supports，也不会自动 finish。
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

无 `skill.json` 的目录是纯方法 Skill。能力 Skill 必须在 manifest 声明 `kind=capability`、至少一个 capabilities，并只引用 `capabilities.py` 中已经注册且可用的能力。所有 prompt_resources 必须位于 Skill 目录内。实际执行才用 `load_for_capability()` 读取正文和 reference；重名、缺 description、路径越界、依赖循环、未知/不可用 capability、重复选择或能力不匹配都拒绝。

固定研究把实际加载的摘要写进 `run_steps.skill_selection`，随后 Writer 按摘要重新加载内容并纳入输入哈希；交互 Agent 只把摘要写入 session event，续问会重新加载并核对哈希。审计不保存 Skill 正文、reference、提示全文或隐藏推理。详见 `docs/14-skill-on-demand.md`。

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

### 10.3 CLI 契约

```text
ask TICKER --input InteractiveQuestion.json [--model MODEL]
ask-followup SESSION_ID --input InteractiveFollowup.json
ask-status SESSION_ID
memo-save SESSION_ID --input MemoRequest.json
memo-list TICKER
memo-read MEMO_ID
```

`ask`、`ask-followup` 和 `ask-status` 只读或写 Agent 会话结果；`memo-save` 是唯一把交互结果保存为公司长期 Memo 的入口。当前版本不提供 agent-promote，也不把临时结果自动转换成 `ResearchDraft`。`InteractiveQuestion.focus` 支持 `general`、`challenge`、`macro`、`peer`、`capacity_cycle` 和 `inventory_cycle`，角色共享同一工具边界。

### 10.4 Agent 工具和模型协议

当前只开放 `get_run_status`、`list_sources`、`read_source`、`search_archived_sources`、`read_report`、`list_memos`、`read_memo`。工具参数使用 Pydantic，工具结果有字符上限，循环有最大迭代次数和 token 预算。模型通过 JSON 决策协议选择 `tool_call`、`final` 或 `ask_user`；DeepSeek 由 Python 直接调用，不需要 OpenCode、MCP 或 LangGraph。

`allow_network=true` 当前明确拒绝，并提示先使用固定 `collect`/`source-add`。未来若增加受控联网工具，必须单独定义权限、来源归档和 `as_of` 规则。

### 10.5 独立运行要求

用户只提供 `DEEPSEEK_API_KEY`、可选的 `WONDER_DEEPSEEK_MODEL`，以及已归档资料或富途/OpenD 配置，就可以直接运行项目内 Agent CLI。无需 OpenCode 或其他外部宿主；SQLite、`data/`、session 和 Memo 是独立运行后的事实来源。

实现交互 Agent 时，参考用户指定的 [Dexter](https://github.com/virattt/dexter) 的 Agent loop、tool registry、skills registry 和 scratchpad，但不复制其 Bun/TypeScript 技术栈，也不复制开放网络、股票筛选、估值/目标价或交易行为。
