# wonder-stockresearch 实施计划

状态：阶段 1–18 已实现；阶段 16.1 已修复富途嵌套年度字段、年报页级检索和年报 Capex 输入；三家重写草稿均通过当前校验，中国移动的 Q09 状态标签已在可恢复重试中修正。三家公司均保持 draft，等待用户进行真实验收后决定是否冻结。阶段 17 已完成按需 Skill Catalog、Capability Registry、执行审计和 Writer/Agent 上下文加载。阶段 18 已扩展既有 Python Agent loop：来源仲裁、工作记忆、长期记忆索引与受限 `AnalysisWorkspace` 均已落地；不引入 LangGraph、多 Agent 或 Pi runtime。
目标：在独立项目中，从零实现可追溯的单股公司研究与手动复评，每阶段提供可运行代码和中文讲解。

## 1. 已确认决策

| 决策 | 最终选择 |
| --- | --- |
| 项目位置 | 独立仓库根目录（例如 `~/Projects/wonder-stockresearch`），与旧项目并列 |
| 主要任务 | 指定股票公司研究、保存历史、手动复评 |
| 数据对象 | 手动维护股票清单；港股和美股 |
| 数据获取 | 自动为主，允许手工补充 |
| 公司框架 | Standard 公司部分 Q01–Q24、G1/G2/G3/G5/G6/G7、M1–M6 |
| 估值 | Q25–Q28 独立，具体如何做以后讨论；首版不实现估值报告或模型 |
| 股东信 | 不做全文研读 |
| 存储 | SQLite + 文件，每只股票一个文件夹、每次研究独立版本 |
| 教学 | 每阶段代码、测试、中文 Markdown 完成后暂停 |
| 运行方式 | Python CLI、Skill Registry、`chat`、固定研究与交互 Agent 由本项目独立运行；不依赖 OpenCode 或其他外部宿主 |

后期明确将估值延后，覆盖先前“首版只看估值指标”的讨论；行情可以作为原始上下文保存，但不生成买卖结论。先前的单 MCP 方案已由 CLI 优先方案替代。不得恢复旧讨论中已删除的功能。

## 2. 最小架构与默认值

Python 3.11、uv、Pydantic 2、sqlite3、argparse、pytest；requests、pypdf、futu-api 仅在用到时引入。Python 包名 wonder_stockresearch，CLI 名称 wonder-research。完整研究能力在项目自身的 Python 进程内；用户自建 Agent 可选调用 CLI/API，不需要 OpenCode、Pi 或其他外部宿主。

一个本地数据库、一套 Python 服务、按股票组织的原文与报告。使用草稿/完成/失败状态、来源 ID、原文哈希与版本历史；自然语言入口只增加轻量 Router、Skill Registry、进度事件和 chat session，不引入事件溯源、旧版 marker 发布、通用工作流框架、MCP、模型 SDK、ORM、多 Agent 或向量库。后续若出现真实的长流程恢复需求，只能在本计划的可选编排阶段按门槛引入，不得提前把核心业务改造成多 Agent 系统。

技术细节以 [技术规格](docs/technical-spec.md) 为准，研究规则以 [研究规格](docs/research-spec.md) 为准。所有首次定义的工程默认值都在规格中明示，不代表用户亲自选择过每个字段。

## 2.1 编排与演进决策（2026-09-20）

1. **领域核心保持函数式**：模型、来源校验、时间边界、评分、报告渲染、完成冻结和数据库事务由 Python 服务负责；任何模型或 Agent 都不能直接改写这些规则。
2. **阶段 4 增加轻量运行步骤账本**：在 `runs` 的粗粒度状态之外记录 `run_steps`，用于收集任务、失败重试、输入哈希、输出引用和 provider 错误；它不是事件溯源，也不是通用工作流引擎。
3. **项目独立运行，不依赖外部宿主**：固定研究、Skill 加载、自然语言入口和交互 Agent 都由 Python CLI/服务、DeepSeek API 与已配置数据源运行；研究事实、草稿、Agent 会话和历史必须可由 SQLite 与 `data/` 重建。OpenCode 集成是历史阶段产物，不是产品运行路径。
4. **直接模型写作先于 Agent 编排**：若只需要从已归档材料生成 `ResearchDraft`，优先实现一个小型 writer 入口，调用现有保存、校验和完成服务，不引入 LangGraph。
5. **LangGraph 仅为可选后置层**：只有在端到端实跑证明需要“自主选择工具、动态补证据、跨会话暂停/恢复、有限修复循环或多步并行”时，才新增独立 orchestration 包。它只能调用现有 Python 服务，不能直接操作 SQLite、计算评分或生成最终报告。
6. **Temporal/Prefect 暂不进入首版**：没有后台调度、多用户、远程 worker 或长期运行任务前，不增加基础设施级编排依赖。

这组决策以“先测出实际复杂度，再增加编排能力”为默认路径；不因 Agent 框架流行而提前改变产品范围。

## 3. 七个教学阶段

### 阶段 1：离线公司研究闭环

- 建立最小 Python 包、uv/pyproject、pytest 和 CLI。
- 实现 ResearchDraft、来源引用、问题/门闸/评分模型及纯校验/渲染函数。
- 首先支持 validate-packet 与 render-packet，输入离线 JSON，输出校验结果与临时报告；这些产物不是正式完成记录。
- 使用虚构公司 SYNTHETIC，不拿模拟数字冒充中国电信事实。fixture 内含完整答案、来源、反证以及缺数据分支。
- 验收：24 问/6 门闸/6 维度覆盖，重复编号、非法分数、引用不存在、缺数字单位被拒绝；null 分数不变成零；估值字段不能进入公司评分输入。
- 文档：docs/01-minimal-research.md；明确函数调用链，给出读代码和改 fixture 的练习。

### 阶段 2：SQLite、草稿和股票历史

- 实现股票清单、runs/sources/run_sources 四类表、schema version、数据目录与相对路径。
- 实现 watch、start、status、history、save-draft、validate、finish、resume；先使用离线材料，不接真实服务。
- 报告只能由服务从已校验草稿生成；最终状态只在产物写入成功后设置。完成的研究不可覆盖；新复评关联旧 run。
- 验收：重启后可读取草稿；同日多次 run 不冲突；保存失败不误标完成；同一 run 完成重试不重复版本；不同股票的基准引用被拒绝。
- 文档：docs/02-storage-and-history.md；讲 SQL、事务、文件与数据库的权威边界。

### 阶段 3：来源存档与证据定位

- 实现 source-add、sources、source-read；支持用户提供 URL/本地文件和元数据，存原始字节、SHA-256 与提取文本。
- 使用 requests 获取 HTTP(S)、pypdf 提取文字型 PDF；HTML 使用标准库解析并保留原文，网页脚本运行/OCR 不进入首版。
- 返回页码或提取文本行号，引用指向原始来源。导入只读材料并归档，不擅自填写发布日期/获取时间。
- 验收：相同字节去重但保留各次取得信息；PDF 无文本报告 extraction_unavailable；已知未来来源拒绝关联；未知发布日期只能作背景；路径越界与过大下载拒绝。
- 文档：docs/03-sources-and-evidence.md；讲证据强度、时间与“校验通过≠结论正确”。

### 阶段 4：真实资料与富途只读接入

- 在 `collect` 前增加最小 `run_steps` 账本及迁移；每个采集子任务记录状态、attempt、输入哈希、输出引用、provider、耗时和错误。不要实现通用任务队列、事件重放或 Agent 循环。
- 实现 collect：报价快照、财务原始数据、最近年报/中报或季报发现；先验证 HK.00728 单只，再用美股 fixture 验证代码、时间和币种。
- 用专用新代码封装行情 context，不导入旧项目账户读取模块。仅调用所需的行情/财务 API；第三方 SDK 不在离线导入时连接服务。
- SEC/HKEX 通过公开官方渠道发现资料，IR 链接由用户或本项目 Python 工具补充。实现前核对当时的官方接口、访问条款和 SDK 能力；不复制未经确认的旧端点。
- 默认语料基线：最近 5 个财年完整年报、最近 3 份中期/季度定期报告、富途最多 10 个年度的结构化四类财务数据；有历史主营构成可选期时，归档最多 10 个年报期和最近 3 个中报期。
- 财务数据保留完整期间、币种、单位、会计口径和 endpoint/field；不按列表下标拼财年，不虚构十年覆盖，不加入 OE/DCF 模型。
- 对查询结果与下载分别留状态。来源发现失败不等于没有公告；网络错误与字段缺失写入结果，允许继续手动补源。
- 验收：fixture 测试覆盖超时、权限不足、空结果、单位/期间差异；真实只读 smoke 单独记录。若公开通道需要用户未提供的非秘密注册信息，仅对应通道标阻塞，其他能力继续。
- 文档：docs/04-data-connectors.md；记录实际验证版本、真实结果与覆盖限制。

### 阶段 5：OpenCode + CLI 端到端研究（历史集成阶段）

- 在 skills/company-research/ 编写新版 skill，只引用本项目研究规格与现有 CLI；安装发现入口按当时 OpenCode 官方规则设置，保持唯一正文。
- 使用用户已有的宿主认证，不复制凭据或把模型接进 Python。
- 流程：开始 run → 获取与读取资料 → 草稿 → 引用/结构校验 → 补缺口或保存完成报告。
- 默认采用人工监督的显式流程，不引入 LangGraph、Temporal、Prefect 或多 Agent；OpenCode 只通过受限 CLI/工具操作，不直接写数据库或报告。
- 来源阅读分段返回，避免把整本 PDF 或全部历史会话塞进上下文；模型不能只读摘要就宣称已核对原文。
- 验收：HK.00728 一次端到端研究，有可定位来源和明确缺口；程序拒绝非法草稿；更换会话后可以读取 run 继续。命令与 skill 不出现不存在的工具。
- 文档：docs/05-opencode-and-tools.md；解释宿主、CLI、Python 与数据库的职责。
- 本阶段仅记录历史兼容集成；OpenCode 不是项目依赖或后续 Agent 的交付要求。未被用户重新要求时，不扩展 OpenCode/Pi/MCP 集成。

### 阶段 6：手动复评、备份与维护

- 实现 start --baseline、compare 和 backup；review.md 比较原报告与新报告的证据、门闸、评分与判断变化。
- Python 计算字段与数值差异；模型提交变化解释及新证据，不用价格变动解释公司分数变化。
- 用 SQLite backup API 创建一致性数据库备份，再复制只读原文与完成报告；CLI 写入采用项目级写锁，备份期间禁止业务写入。
- 验收：旧材料可作为历史，不能伪称新获取；旧报告不变；缺分值的差值显示不适用；备份恢复到临时根目录可打开历史、原文和报告。
- 文档：docs/06-review-and-maintenance.md；README 补完整实际安装/运行路径。

### 阶段 7：DeepSeek 直接写作入口（本版本必做）

- 新增一个小型 Python writer：从指定 run 的已归档来源、分段原文与研究规则构造受控上下文，调用 DeepSeek，产出既有 `ResearchDraft`。
- 新增规划中的 `write-draft RUN_ID --provider deepseek [--model MODEL]` CLI。它只保存草稿并执行既有 `validate`；不得自动 `finish`，正式归档仍需用户显式执行 `finish`。
- 只接一个供应商，不建通用 provider/插件框架，不增加自主检索、工具循环、MCP 或 LangGraph。模型只能使用已有 source_id 与 locator；Python 继续负责来源时间边界、引用校验、评分、报告渲染和事务。
- 凭据只从运行环境的 `DEEPSEEK_API_KEY` 读取；不写入项目、日志、SQLite、`run_steps` 或对话。实际端点、模型与参数在实施前以当时官方文档核对，不在本计划预填。
- 在 `run_steps`（阶段 7 通过显式 v2→v3 migration 增加的受控 metadata）记录 provider、模型、输入哈希、模板版本、受控输出引用和可取得的用量摘要；不记录认证头、完整提示词、隐藏推理或未归档大段原文。
- 验收：fake HTTP 覆盖成功草稿、超时、HTTP 失败、非法 JSON/schema、未知来源引用和校验失败；失败不产生 completed 报告。用户准备好环境后，另做一次显式、最小的真实 API smoke 并记录结果与限制。
- 文档：docs/07-deepseek-writer.md；说明数据流、环境变量、调用预算、人工定稿与不能证明的事。

### 阶段 11：进度事件（已实现）

- 为 `collect`、`write-draft`、`ask` 和后续 `chat` 增加可选 `ProgressCallback`。
- 默认将简短阶段状态写入 stderr，stdout 继续保持最终 JSON；不输出隐藏推理、完整原文或完整工具结果。
- 新增 `ProgressEvent`，为未来 UI 或外部 Gateway 保留稳定事件形状。

### 阶段 12：独立自然语言入口（已实现）

- 新增 Python `Skill Registry`，独立读取项目 `skills/**/SKILL.md`；Skill 只提供模型上下文，不提供程序权限。
- 新增结构化 `RoutePlan` 和 DeepSeek Router，支持不严格中文表达，区分正式研究、交互问题、Memo 和澄清。
- 新增 `chat` 命令，支持连续交互和 `--text` 单次输入；chat session 持久化到 schema v5 的 `chat_sessions`。
- 正式研究自然语言流程为：识别公司 → 用户确认 → start → 自动只读 collect → write-draft → validate → 用户确认 → finish。
- 意图不清、股票代码不确定或数据采集全部阻塞时必须停下并解释；不猜测、不自动 finish。
- 当前只保留一个完整交互 Agent；Router 是一次受控分类调用，正式报告 Writer 是一次受控写作调用。
- 宏观、产能、库存周期和同行业专用 Agent 本阶段不新增；同行 Agent 等用户提供具体研究方法后再单独设计。
- 文档：docs/10-standalone-natural-language.md。

### 阶段 13：三家电信公司数据语料与覆盖核验（已完成，2026-09-26）

- 目标标的：中国移动 `HK.00941`、中国联通 `HK.00762`、中国电信 `HK.00728`。
- 从 HKEX 官方渠道归档最近五个完整财年的年报（当前目标 FY2021–FY2025）和最近三份中期/季度报告（有资料时目标为 2024H1、2025H1、2026H1）。不以公告检索“无结果”推断公司没有披露；逐份核对覆盖并记录缺项。
- 归档富途只读财务数据：最多 10 个年度期间的利润表、资产负债表、现金流量表和关键指标；主营构成在接口提供历史期间时归档最多 10 个年报期与最近 3 个中报期，并保留 period、date、currency、accounting standard 和原始字段。
- 只运行项目现有 Python CLI/服务，不调用 OpenCode、外部 coding agent 或交易/账户接口；不生成正式报告、不测试 Agent 编排，也不在本阶段实现同行分析。
- 对三家公司逐项审计报告年度/期间、PDF 可读取状态、结构化财务期间数量与币种、缺失/失败原因；数据及本地报告只写入 `data/` 并保持 Git 忽略。
- 验收：离线选择逻辑和历史期间解析测试通过；真实来源逐家公司核对。OpenD 或网络不可用时明确标记阻塞，不伪称数据已补齐。完成后暂停，下一阶段再由用户决定是否用三家公司验证 DeepSeek 草稿质量。
- 说明文档：更新 `docs/04-data-connectors.md`、`docs/08-futu-data-capabilities.md` 和本文件的 `PROGRESS.md` 实测记录。

完成结果与逐家公司覆盖见 `PROGRESS.md` 的阶段 13 实测记录；数据仅写入 Git 忽略的 `data/`。本项目及后续阶段不要求 OpenCode 或其他外部宿主。

### 阶段 14：定稿反馈与正式报告可读性（已实现，2026-09-27）

- 交互式终端完成 `finish` 时显示状态、质量、分数和绝对产物路径；复评报告存在时一并列出。自然语言 `chat` 完成正式报告时使用相同摘要，不重复打印 session_id 或原始 JSON。
- stdout 非 TTY 时 `finish` 保持既有 `{ok,data,issues}` JSON 契约；进度仍写 stderr。绝对路径基于项目根和 run 目录解析，适用于路径中带空格的项目。
- 后续生成的 `company.md` 为 Q01–Q24 显示研究规格中的完整问题文本；答案状态、陈述类别、证据关系及相关状态标签使用中文。
- 数值按千位分组，保留 Decimal 精度，不换币种、不舍入；unit 与 currency 相同时仅显示一次。
- 正文引用使用按 source_id 排序生成的短编号，来源索引保留完整 source_id、标题、类别、日期、提取状态和来源地址。完全相同的引用去重；locator 或证据关系不同的引用保留。数值证据与所属陈述完全相同的引用只显示一次。
- 不修改 ResearchDraft、SQLite schema、证据校验或评分规则；已完成 run 的 company.md/research.json/review.md 继续冻结，版式更新只作用于以后新生成的报告。
- 不修改 Writer Prompt、不调用 DeepSeek、不重写已有报告。报告中的问题与回答是否实质匹配仍需另行做研究质量验证。
- 教学说明：[docs/11-report-presentation.md](docs/11-report-presentation.md)。验证结果记录在 `PROGRESS.md` 阶段 14。

### 阶段 15：Writer 问题对齐与校验失败反馈（已实现，2026-09-27）

- 将 Q01–Q24 的完整问题文本集中定义，并由正式报告渲染器和 DeepSeek Writer 共用，避免模型只看编号猜题意。
- Writer 对每一问增加直接回答题意的要求；特别约束 Q03 必须分析资本开支金额/收入比例/期间/口径，不能用利润率、ROE、净利润或 EPS 替代；Q04 必须分析技术替代/产品变化速度及其竞争影响，不能用 EPS、利润率或估值指标替代。证据不足必须降为 partial/missing。
- 加强 `as_of` 证据边界提示：context 快照不得支撑 answered 或非 null 护城河分；M1 需要单位成本/成本结构优势的直接证据，规模、网络覆盖或牌照本身不等于成本优势。
- 对固定研究的 `validation_failed`，终端显示具体字段位置、错误码、解释和 run_id；尚未通过校验时明确告知没有生成/冻结正式报告。非 TTY `chat --text` 以 `ok=false`、issues 数组和非零退出码返回，保留可解析 JSON。
- 不放宽来源/时间/事实校验，不自动把 context 改成 supports，不自动 finish。已有失败草稿和已完成历史报告保持原状；需要重新生成时由用户决定是否在同一 draft run 上再次调用 DeepSeek。
- Writer 模板版本升级为 `deepseek-draft-v3`，令提示变化反映在 input hash 与 run-step 元数据中。
- 文档：[docs/12-question-alignment-and-validation-feedback.md](docs/12-question-alignment-and-validation-feedback.md)。

### 阶段 16：固定研究质量流水线（工程已实现，真实质量待用户验收，2026-09-30）

- 六组 EvidencePack、Provider 定向抽取、PDF/文本确定性排序和证据缺口。
- Decimal 可信计算：净利润同比、资本开支/收入、ROE 序列与波动摘要；模型不能覆盖 Python 结果。
- `write-draft` 默认 staged：六组 Answers、一次综合判断、语义审查、最多 5 个单元各修订一次和最终审查；每次尝试硬上限 14 调用。
- schema v6 增加 `draft_sections` 与 `draft_reviews`；分组按输入哈希恢复，草稿改变使旧审查失效。
- 新 standard run 的 `finish` 需要 matching passing review；完成后冻结 `quality-review.json`。schema v5 旧 run 不受影响。
- 新增 `review-draft`、`--strategy staged|one-shot`，`status` 显示分组、预算、草稿哈希和审查状态。
- 中国移动、联通、电信三份新 run 均覆盖 24 Q、6 Gate、6 Moat，并在阶段 16.1 重写后通过当前自动审查和 `validate`；三份均未 finish，详见 [docs/13-fixed-research-quality.md](docs/13-fixed-research-quality.md) 和 `PROGRESS.md`。

### 阶段 16.1：定向证据恢复（实施中，2026-10-01）

- 修复富途 `report_list → item_list` 的期间、币种、会计准则继承，十年 ROE 成为可追溯的可信计算输入。
- 在 schema v7 增加本地 FTS5 页级索引；每题先保留证据，再在固定字符和来源配额内扩展，避免高频词挤掉其他问题。
- 从年报的明确当年 Capex 披露计算 Capex/收入，现金流购买资产仅作为单独标明的 proxy；三家电信均恢复 2021–2025 的比率序列。
- 重写三份草稿并人工核对 Q03、Q04、Q05、Q07、Q09、Q14、Q21、Q23；不自动 `finish`。完成后更新真实结果并暂停。

### 阶段 17：Skill 按需加载（已完成，2026-10-04）

- 启动时只扫描 name/description/path/availability；Router 只接收摘要，选中后才加载正文与其声明的 references。
- 方法 Skill 新增目录即可发现；能力 Skill 必须引用 Python Capability Registry 中显式注册的受控能力。
- 新增 `skill-list`、`skill-check`、`start --skill`，记录 Skill 名称、版本和内容哈希；拒绝路径越界、重名、依赖循环和未知 capability。
- Router 只收到 name、description、path、kind、适用 capability 和可用性摘要；实际执行才加载正文/reference。固定 Writer 与交互 Agent 只保存名称、版本、内容哈希等审计摘要，不保存正文。
- 当前能力为 `fixed_research`、`interactive_research` 和尚不可用的 `analysis_workspace`；后者只能由阶段 18 实现后启用。详见 `docs/14-skill-on-demand.md`。

### 阶段 18：开放问题 Agent、记忆与受限临时计算（已实现，2026-10-04）

- 固定研究继续使用 EvidencePack、可信计算、Writer、审查和 finish 门闸；只有既有交互 Agent loop 可以使用临时计算，且不会修改 `ResearchRun`、评分、门闸或 finish。
- 新增双层来源策略：获取时优先富途结构化与官网结构化资料，权威判断时官方披露优先；搜索发现记录不能作为 supports。富途数值与同 run 官方结构化事实核对，`matched`、`provider_unverified` 和 `conflict` 都可追溯；未在草稿 conflicts 中明确解决的冲突阻止 validate/finish。
- `market` 只获取快照；`futu_fundamentals.statements` 是唯一的财务报表入口，使用 Futu `next_key` 分页、每类最多 20 个去重年度期。标准采集不再自动取得 valuation/consensus。
- schema v8 增加 Scratchpad、带 FTS 的记忆索引、analysis 执行审计与来源冲突；follow-up 只加载必要的工作状态和最多五条相关记忆，临时报告始终是 provisional。
- 新增 `analysis_workspace` capability 和同名 Skill。它优先调用项目可信计算；不足时只对最小只读 JSON 副本执行受限 Python AST，记录数据集/代码哈希、结果和来源，限制十秒、256 MB、64 KB 输出以及每 session 三次执行。
- `memory-search`、`memory-read` 和扩展后的 `ask-status` 可检查保存内容、Scratchpad、计算与分析审计。详见 [docs/15-open-question-agent.md](docs/15-open-question-agent.md)。

### 阶段 19–21：新数据、运行时评估与专业能力（长期）

- 阶段 19 按需求增加 SEC XBRL、同行、估值、OCR、新数据源和专题 Skill；先定方法和验收，再增加 capability。OCR 只处理原生提取失败或表格严重损坏的页。
- 阶段 20 先以二十个开放问题比较当前 Python loop 与 `AnalysisWorkspace`，再按门槛比较 Pi Agent Core、Pi + 虚拟文件系统/受限 shell，以及需要更强隔离时的容器/E2B；Pi 若采用，只替换开放问题的 orchestration/runtime。
- 阶段 21 从完成报告确定性生成带来源、as_of 和失效条件的事实记忆；只有质量或延迟有明确收益时，才用 `asyncio.TaskGroup` 加入最多三名受控 worker。Python 研究核心继续权威。

## 4. 每阶段完成要求

参照 [教学约定](docs/teaching-contract.md)。最小实现 → 有意义的测试 → 实际运行 → 中文讲解 → 更新 PROGRESS。阶段 1 不顺手实现全部数据库或数据接口。

完整版本验收：从股票代码得到公司研究报告及归档，再发起一次有历史对照的复评；并可对已有归档材料调用 DeepSeek 生成可校验草稿，再由用户显式定稿。公司/估值分离、来源/缺口、版本保存、离线测试和 API 失败不绕过校验均成立。软件仍不保证所有资料自动可得，也不保证模型判断正确。

## 5. 后续候选，非当前交互层待办

独立估值规格、同行业竞争 Agent、用户自建 Agent 与 Python 服务间的集成协议、额外数据源、OCR、筛选和日常监控，以及满足门槛后评估的 LangGraph 编排。宏观/产能/库存周期 Agent 暂不进入实现队列。交互 Agent 和自然语言入口已实现，不再属于当前待实现候选。OpenCode 与其他外部宿主集成不列为项目必需工作。

## 6. 阶段 7 的模型 API 写作方向（2026-09-21 决策，已实现）

用户确认本版本完成前必须可直接接 DeepSeek API，让 Python 调用 LLM 生成研究草稿；该能力已在阶段 7 实现并通过真实 API smoke。以下记录保留当时确定的设计边界。

阶段 7 选择 **Python 直接调用 DeepSeek 的写作入口**，使报告写作由项目自身完成，不依赖外部宿主会话。核心服务保持独立运行，研究上下文可由数据库和来源片段重建，不能只存在聊天里。

直接接入的最小任务是：读取已收集的材料与规则 → 调用模型产生 `ResearchDraft` → 通过现有 save-draft/validate 服务保存和校验 → 用户显式 `finish`。调用超时或不合格输出不得绕过校验；不是让模型任意覆盖 Markdown。首次只接一个供应商，不预建复杂多模型框架。完整自动检索或后台调度属于另外的范围，不因“接 API 写报告”自动实现。

具体接口边界、凭据处理和验收要求见技术规格第 8 节。模型名称、端点与预算在实际接入时核对和确定，不在当前硬编码。

## 7. 报告层级与附记（用户 2026-09-18 决定）

报告按重要性分三层，标准公司研究定稿后永不修改：

1. **标准公司研究（季报级）**：Q01–Q24、六项公司门闸、M1–M6，即上文的“护城河报告”。完成后冻结，只能新开一份或在其下挂附记，不能回改正文。
2. **附记**：季度之间单独存一条，挂在某份已完成的标准研究之下，不改正文；只按最新价格/资料重跑一类的更新属于此类。**估值内容放在附记**：本阶段不实现估值（Q25–Q28/G4 仍 deferred），但数据结构和文档预留位置，估值不进公司分数。
3. **专题报告**：同行竞争对比、新闻解读等非标准研究，单独成报，不要求完整 24 问结构；本阶段不实现，列入后续候选。

阶段 2 只落“标准研究 + 可挂附记”的挂靠结构（runs 的 kind、parent/挂靠关系与预留字段），不实现附记正文、估值计算或专题报告。查看时按标准研究归并显示其附记，但附记是独立记录。

## 8. 并行研究模式：固定流程与交互 Agent

固定研究流程和交互 Agent 是两个并行入口，不是前后依赖关系：

```text
固定 Prompt/CLI → 标准 ResearchRun → validate → finish → 冻结正式报告
开放问题       → AgentSession → InteractiveReport → 用户明确选择后 Memo
```

交互 Agent 可以读取正式报告、历史、来源和 Memo，但不得自动修改 ResearchRun、评分、门闸或执行 `finish`。如果用户要更新正式报告，必须重新启动新的固定研究 run。

阶段 8–12 已实现：

- 阶段 8：独立 Agent session、有限 JSON 决策循环、只读工具注册表、调用轨迹和 DeepSeek 直接调用。
- 阶段 9：`InteractiveReport` 临时报告和 `ask`/`ask-followup`/`ask-status` 命令。
- 阶段 10：用户明确调用 `memo-save` 后保存公司 Memo，以及 `memo-list`/`memo-read` 查询。
- 阶段 11：`ProgressEvent`、stderr 简短进度和 `--quiet`。
- 阶段 12：独立 Skill Registry、RoutePlan、`chat`、非严格自然语言确认、可恢复 chat session、终端内复用 session 的连续对话循环，以及简称标的候选搜索和用户消歧。

当前 CLI 不依赖 OpenCode：

```bash
wonder-research --root <项目根> ask HK.00728 --input question.json
wonder-research --root <项目根> ask-followup <SESSION_ID> --input followup.json
wonder-research --root <项目根> memo-save <SESSION_ID> --input memo.json
wonder-research --root <项目根> chat
wonder-research --root <项目根> chat --text "帮我研究一下中国移动"
```

实现交互 Agent 时，必须参考用户指定的 [Dexter](https://github.com/virattt/dexter) 的主循环、工具注册表、Skills 和 Scratchpad 设计，但保持本项目的 Python/CLI/SQLite 技术栈和证据边界。参考链接与改编边界见 `docs/09-agent-layer.md` 和 `AGENTS.md`。

## 9. 后续专业工具与 LangGraph 门槛

反方仍可作为当前交互 Agent 的开放问题；宏观、同行、产能和库存周期专用 Agent 本阶段暂不实现。下一项专业 Agent 优先考虑同行业竞争，但必须等用户提供具体研究方法、比较对象和数据范围后再设计。只有真实使用证明不同角色需要独立上下文、并行执行或独立质量评估时，才拆分为子 Agent。

LangGraph 仍不是当前实现依赖。只有多轮工具调用恢复、跨天状态、并行任务或审计复杂度已经超过现有有限 Python 循环时，才评估 LangGraph。若引入，它只能编排现有服务和工具，不能直接写数据库、计算分数或生成完成报告。
