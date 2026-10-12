> 历史快照：截至2026-10-12整理前，仅供追溯；现行规则请读仓库根入口。正文保留，相对Markdown链接已重定位。

# wonder-stockresearch 实施计划

## 2026-10-11：澄清 Skill 与五题通用修复（离线实现，真实质量待复测）

用户明确要求创建 Skill，并结合前述计划一起解决五题问题，本轮按最新指令合并实施 G1–G3 正确性部分及 G4 离线回归，不再逐小步停下。实际实现和中文教学见 [问题澄清与数值回答](../../40-question-clarification-and-numeric-answers.md)。

- G1：修正无工具收尾的能力误述；AST 拒绝反馈具体调用名；常用统计无需模型写代码；格式/引用/数值提交预算内最多纠正一次，跨 Attempt 不清零。
- G2：创建无权限 `question-clarification` method，接入 ask/chat 共用入口及两种 runtime。冻结分析时钟和方法哈希，记录目标年份/季度与待确认项，明确年份直接执行，“近几年”先列具体候选并确认。补充恢复原 Turn；资料缺年不替换目标，自然年与未知/非12月期末的财年不能混用。
- G2：成功 SQL 保存带身份的原始 verified_facts，numeric_blocks 指向本 Turn 真实输入；Python 核对口径/期间/回执，Decimal 计算并渲染原值、均值、极差、总体标准差及变化。排序不拆身份，未知单位不补百分号，未知单位/倍率不跨来源混算，正文不另抄数值趋势。
- G3：通用别名候选排序与原值优先，字段目录指向真实取值；返回实际归档覆盖并阻止同范围已知无季度的等价空查，其他授权来源/补取路径保留。chat 等待、失败和追问关联分别恢复正确 Turn。
- G4：新增虚构公司、多指标、两种 runtime 的离线回归及三个 Skill 评测提示；完整实际结果见 PROGRESS。离线契约通过不等于真实五题答对，真实 Skill A/B 未执行。

下一步：按原来源/预算真实复测五题，将必要澄清记为交互步骤，分别审查年份、单位、数值、引用、覆盖和恢复；真实调用需新的明确额度，旧 Pi 40/40 不重置。本阶段未新增模型/OpenD请求、主库迁移、Git提交或默认 runtime/thinking 切换。D1 公司展开视图、D2 SQL 下推/快照复用/性能测量和有依据的单位映射继续保留，未宣称完成。复杂财历/滚动窗口仍由同一 Agent 澄清，不引入向量库或多Agent。

## 2026-10-11：真实五题复盘后的通用修复路线（设计基准，实施进度见上方）

已核对用户最新 ask 五题结果及只读工具/归档记录。四题 completed 不等于答对：Q20 统计量算对但年份和值配反，R01 主期间不稳定且把 2024→2025 下降写成连续回升；Q11 受限代码拒绝后误称缺计算能力，Q08 反复查目录并编写 locator 被拒，Q23 年度归档无季度但重复空查。详细证据、函数/文件职责和跨场景验收见 [五题复盘与通用修复计划](../../39-five-question-review-and-general-plan.md)。

用户补充的“近三年”澄清纳入 G2：程序提供分析基准日期与财年/披露覆盖，结构化解析相对时间，分开目标期间与可得期间；明确年份或会话已确认口径直接执行，有多种合理解释/资料滞后/披露状态不明时用现有 ask_user 暂停并列出年份候选，确认后同 Turn 恢复，不默默取旧三年。2026年且2025完整财年已披露时最近三个为2023–2025；不是全公司写死这三个年份。详见39计划3.3.1及固定时钟/多财年验收。

相对时间的具体架构分工见39计划3.3.2：现有模型经 prompt 提出结构化任务含义，Python 提供日期/覆盖并解析已确定期间、强制必要澄清和最终核对；不按短语硬编码年份，不仅靠模型自报置信度。RAG用于补事实依据，可选语义复核模型仅为后续备选，当前不新增向量库或多Agent。

按用户建议，G2 加入借鉴 grillme 的小型 `question-clarification` 方法 Skill，详细接入与验收见39计划3.3.3。每题检查对象、指标/口径、期间和交付目标，仅对会改变答案的必要歧义追问；可查证的资料先只读查询，确认结果持久化复用。Skill 指导现有 Agent 理解问题，Python 校验并阻止带未解决必要选择的计算/报告接收；不靠 Skill 承诺完全理解。直接 ask 当前默认不加载 Skill，因此拟接入 ask/chat 共用执行层，覆盖 legacy/Pi 和恢复路径，不只由 Router 选择，不新增 Agent、工具权限或每题额外模型调用。验收同时检查漏问与不必要追问；本轮仅记录设计，未创建 Skill 文件或修改业务实现。

用户明确要求 general 方法。后续实现不得按公司、ticker、题号、问题原文或特定数值分支；回归样例与产品规则分离。采用“带事实身份的输入 → 通用 SQL/受限函数 → 结构化计算产物 → 程序校验并渲染数字/期间/单位/趋势 → 模型解释”的共用链路，不添加公司专用计算器，也不放宽引用/任意 Python 权限。

最新实施顺序：**G1 受限计算纠正与真实能力状态 → G2 数值/年份身份、单位与趋势 → G3 检索覆盖与无进展循环（含 D2 剩余优化）→ G4 同条件质量验收**。D1 仍保留，优先级低于已证实的正确性问题；下方 D1→D2→D3 为历史分解，最新顺序以本节为准。每次一个可独立验收的教学阶段，完成代码、必要测试、讲解与进度后暂停。本轮只复盘和更新计划，未开始业务修复或新增真实执行。

## 2026-10-11：五题初诊后的下一步（计划，未实施）

用户运行 chat 五题：腾讯 ROE 等待选股；Q08 因 missing_turn_evidence_receipt 失败；Q23 因 claims 中 evidence_refs 不符合协议失败；Q11/Q20 生成回答但单位尚未确认仍写百分号/百分点，completed 不能作为质量通过。本次仅阅读 JSON，尚未完整核验工具 trace、原始数据和期间对应。

下一轮优先围绕真实失败修复：来源与原始指标定义有依据才建立版本化 unit/scale 映射；数值、期间、单位、引用和计算绑定为结构化产物；由程序根据单位确定性渲染比例水平、百分点差和统计量。单位未知只保留原始数值和缺口，不能把用户要求的单位当来源证据。只改 prompt 不能证明任意自然语言正文没有单位错误，需相应收紧结构化数字输出契约并覆盖未知单位/倍率测试。另调查 Q08 目录摘要 locator 与本 Turn 可引用回执的差异、Q23 schema 说明及有界格式纠正；不放宽引用或直接接受任意多余字段。

用户希望再跑五题，已准备 data/evals/diagnostic-5-prepared-20261011 的固定 ticker 输入与调用现有 ask CLI 的脚本。复跑同组五题，legacy、company_latest、allow_network=false、60,000 tokens/10 轮，每题只启动一次，无自动重试；显示回答/失败并保存 JSON 与 reports.md。ask 绕过 Router 和 chat Skill 选择，因此此轮是 Agent 核心诊断，不是同入口严格 A/B。尚未执行真实调用，不从旧 40 次账本扣减或改写历史。

## 2026-10-11：每家公司一个主备忘录与 chat save（离线完成）

用户明确要求临时分析后输入 `save` 完成保存，每家公司只有一个主备忘录。本轮确定性追加原报告，不额外调用模型归纳/重写：保存问题、回答、结论、反方、缺口、下一步及全部原引用；同一报告重复 save 幂等。主备忘录按 ticker 唯一，旧 company_memos 保留为不可覆盖的条目/历史，显式 schema v14 迁移归入主备忘录，不删除旧 ID。公司主备忘录导出到 data/stocks/<ticker>/memo.md。

chat 在 Router/Agent 前识别精确 save；不新增模型/工具写权限，不改正式研究。无会话、当前问题未成功、待确认或来源校验失败时拒绝保存，不能回退保存旧回答。CLI memo-save 保留 JSON 输入，并支持无输入直接保存原会话当前报告；memo-list 每公司最多返回一个主备忘录，memo-read 支持 ticker/主 ID/旧条目 ID。新建索引/条目/长期记忆在同一事务，导出失败可再次 save 修复。离线覆盖两 runtime、连续追问保存、重复命令、恢复、公司隔离、旧库迁移、来源拒绝与故障回滚；写教学与进度后暂停。真实模型/OpenD 调用、主库实际保存/迁移及主题自动合并不在本轮范围。

实际结果：默认/Pi 全量各 **416 passed**，新增 12 项 Memo 用例；Ruff、wheel 构建与隔离加载通过。详见 [中文教学](../../38-company-memo-save.md) 和 PROGRESS。此教学阶段完成后暂停，后续 D1/D2/D3 未自动启动。

## 2026-10-11：ROE trace 复盘后的 Agent 与公司数据优化（P0 与 D2 子集离线完成）

用户先要求将本次复盘建议纳入计划，随后明确要求纳入工作清单和字段检索并开始执行。P0 与 D2 的跨数据集定位指导、紧凑目录、Turn 工作清单子集已完成离线实现，见 [实现与教学](../../37-agent-query-and-recovery.md)。D1、D2 剩余部分、D3 和真实复测仍待实施；不自动启动模型评测、数据刷新或主库迁移。每次只实施一个可独立验收的教学阶段，完成后暂停。

复盘基准：Langfuse trace `40520f2a92bc776537b1c1bab1b02a0a`，chat `d862d46f-3807-4cd0-9bc1-6358a2b56a84`，问题“分析腾讯近四年的 roe 变化”。legacy Agent 先两次查询经营效率目录，再在财务报表目录找到 ROE，并成功取得 2022–2025 年度事实；本次均读取已归档富途资料，没有刷新供应商或阅读年报。后续计算包含不允许的 Lambda，模型收到错误后原样重试，被重复调用检查终止，最终没有回答；本次不是预算耗尽。字段分页还受模型投影大小影响，不能仅凭完整工具结果认定翻页多余。

### P0：失败恢复与 prompt/Skill/tool 契约一致（离线完成）

- 用户最新要求开始本轮实施，并把“跨数据集先找字段”和“已完成/错误/待办工作清单”从 D2 前置纳入本轮验收。D1 视图、SQL 下推/快照复用及 D3 指标映射仍为后续阶段；不新增真实调用额度。
- 已按 [受限计算工具与有界错误恢复详细计划](../../36-calculation-tool-next-plan.md) 实施。默认/Pi 全量各 404 passed，Ruff lint 通过；36 项新增用例验证查询、失败、重复阻止、修改计算及有引用回答。真实 LLM 是否少绕路和稳定回答尚未复测，不用离线通过替代语义质量验收。
- 统一系统提示、Skill 示例与受限计算工具的语法说明。错误返回具体节点/位置及可执行替代方案，例如 SQL 排序或无 Lambda 的数据对排序；保留成功查询的 analysis_id 和引用，不让模型重新采集已取得事实。
- 受控支持 `sorted(..., key=lambda row: ...)` 的简单字段/数值/元组排序，单独验证位置、参数作用域和表达式；保留现有 12 个允许函数，不直接开放全部 Lambda、方法或第三方库。原失败 Lambda 在新规则下应成功，不再把它当作必须拒绝的代码。
- 相同失败调用继续阻止执行，但首次重复时提供一次有界纠正机会；无法修复时输出已核实事实、未完成计算与具体原因。不得无限循环、重复执行失败副作用或重置 Turn 预算。
- chat 在执行前关联 Agent Session/Turn，失败后仍保留关联、错误和恢复入口，避免孤立会话或误发起新问题。
- 财务研究纪律、单位和引用规则由 legacy/Pi 共用；runtime 分支只保留协议差异。工具选择与 Skill 上下文按问题收敛，在新 Turn 冻结允许工具范围。
- 验收：离线覆盖简单排序 Lambda 成功、不支持写法拒绝→纠正→回答、同参阻止后额外反馈、无法纠正的有限收尾及失败 chat 恢复；校验两种 runtime 的预算、授权、引用和冻结规则。只增加总轮数不能替代恢复路径修改。

### D1：公司展开视图与版本语义

- 保留规范化关系 `financial_facts/company_profile_facts.batch_id → source_batches.id → source_batches.ticker`；当前事实表通过批次归属公司，不需要先给每条事实重复存储 ticker。
- 增加便于人工查看和服务查询的只读公司展开视图，带 ticker、dataset、source_id、来源时间、解析版本和事实定位。明确“全部已索引版本”“当前可用版本”“Turn 固定版本”的区别，不让展开视图静默挑最新。
- 验收：多公司同名字段不会串数据，多批次历史保留，失效/不合范围来源不被选中，旧 Turn、正式报告、Memo 和计算快照不换来源；备份/重建后关系可核验。详细设计同步至 [公司数据库计划](../../27-company-database-plan.md)。

### D2：精确查询、紧凑字段目录与快照复用（定位指导/目录/清单已完成，SQL 优化待实施）

- 优先使用已有跨 dataset 字段检索，例如先以 `mode=fields, query=ROE, period_kind=annual` 定位，再按准确字段和期间取值；将这一通用策略写入 Skill，避免猜测指标属于“经营效率”。不为腾讯或四年题写死路径。
- 字段目录默认给覆盖范围、数量、最近期间和续查入口，减少每个字段重复列出全部期间；分页必须与模型实际可见投影一致，保留匹配数、展示数、截断和 next_offset。
- 将公司、来源批次、dataset、字段、期间类型、期间和分页筛选下推 SQLite，目录用 SQL 聚合，减少反复读取全部事实再由 Python 过滤。先测 EXPLAIN 与读行数，再决定复合索引；Agent 仍只访问经 Python 验证的隔离只读快照。
- 在来源哈希、解析版本及范围不变时复用验证结果/查询快照；来源刷新生成新版本，旧 Turn 保持固定。成功结果形成紧凑工作记录，保留字段、期间/值/单位、来源回执、analysis_id、完成步骤和待处理错误。
- 验收：同一来源快照的查询结果与引用前后一致；比较工具次数、实际读取行数、耗时、内存和模型投影 token；覆盖跨 dataset、中文别名、分页、修订版本和压缩后继续计算，不提前承诺提速比例。

### D3：指标口径、单位映射与计算到回答

- 建立小范围、可核验且带版本的指标目录：规范指标/别名关联原始字段、dataset、供应商、原值/同比、期间、会计范围、单位/倍率与证据。原始字段和值继续保存；不同口径的 ROE 不自动合并。
- 单位或倍率缺失时保留 unknown/null，不因名称含 ROE 就补 `%`；核查共用计算中缺失单位的默认值。只有确认百分数口径才输出百分点差，否则保留原始数值与 raw delta，说明限制。
- 计算产物按期间绑定数值、单位、引用和派生结果，由程序验证并渲染到回答；避免平行数组错位和模型重算。趋势描述与原因证据分开，只有 ROE 序列时不自动推断业务成因。
- 验收：离线覆盖原值/同比、年度/中期、单位缺失/倍率差异及多来源口径冲突；最终回答的期间、数值、单位、引用与成功计算产物逐项一致。

### D4：失败观测与同快照复测

- 补足失败工具及执行前阻止事件的观测，关联 chat、Session、Turn、Attempt、runtime，保留原始失败原因与重复调用关系。默认仍用安全摘要，正文仅沿用显式选择，禁止记录密钥和隐藏推理。
- 用本次失败形态建立离线回归，再在同问题、同来源快照和同预算下分项比较数据定位、期间/单位、计算、引用、完成回答、token 和延迟；正确报告缺口单独计分。
- 真实模型评测须另获额度，原 Pi 40/40 不重置；默认 runtime、thinking、研究评分与正式冻结规则不因计划改变。通过相关离线检查后再决定真实复测范围，不将 completed 当作答案质量通过。

本轮实际交付：共用错误恢复/清单、受限排序 Lambda、紧凑字段目录、prompt/Skill/tool 契约和失败 chat 关联；失败/blocked 观测已随 P0 补齐。默认 runtime、thinking、历史数据与 40/40 真实额度不变。按教学约定暂停；下一阶段为 **D1 公司展开视图 → D2 SQL 下推/快照复用 → D3 指标/单位映射及结构化数字回答**，D4 的同快照真实评测需另有额度。

## 2026-10-09—10：混合架构 B——执行边界、分包与 Pi TypeScript 包装（离线完成）

按架构评审第 10、13、15 节开始下一教学阶段。采用 Python 研究核心 + Pi 通用循环；本阶段先完成可独立验收的执行架构整理，后续数字产物渲染和查询快照优化仍各自验收，不借重构悄悄改变回答或研究方法。

1. 抽出共用 Session/Turn/Attempt 操作、预算状态、工具执行与结果接收，消除 Pi 反向引用 Agent 入口的私有函数；旧循环作为兼容适配器调用相同业务操作。
2. 新问题创建时固定运行时；恢复执行以 Turn 快照为准。保留旧历史，不切换默认、不删除旧循环；未知 runtime 或不兼容协议在模型请求前拒绝。
3. 为本地 JSONL 配置、控制请求与结果定义版本及 Pydantic 校验，区分必须等待的业务操作和运行进度通知。Pi 用原生 subscribe/transformContext/followUp 及合适的工具前后钩子连接 Python 操作，不引入 coding-agent/Harness 会话或自建事件框架。
4. 工具直接登记普通 handler，统一授权/参数检查，按需读取数据；统一内部成功/错误结果与模型投影边界，保留原始业务 payload、事实标签、回执和产物。
5. 抽出公共 Python 模型传输，解除 Router/旧问答对 Writer 私有请求实现的依赖；Writer 分组写作与模型参数、预算、错误行为保持兼容。
6. 按用户要求集中在架构整理后验证：离线协议、原生钩子、固定 runtime、未知 usage、恢复/中断、授权与冻结回归，再运行两条路径全量测试、Ruff、打包/文档检查。更新实际架构与中文教学、PROGRESS 后暂停。

2026-10-10 用户追加并分别确认：本轮纳入 Python 分包（`fixed` / `interactive` / `interactive.pi`），固定与问答共用计算先提至按职责命名的公共模块；CLI、公开 `agent.py`、数据源与数据库入口保持稳定，不为每个旧内部模块创建 shim。用户另明确同意仅将 `pi_runtime/runner.mjs` 的 SDK 包装源码改成 `src/runner.ts`，增加项目内锁定开发依赖、类型检查和构建；Python 启动构建后的 JavaScript，Pi runtime 目录不移动。迁移直接同步代码、测试、脚本和文档引用，完成后再集中复验。研究契约、提示词、默认 runtime 与真实额度不借此改变。

真实模型额度仍为 40/40，不新增模型/OpenD 调用、主库迁移或 Git 提交。除用户追加确认的 Pi TypeScript 编译开发依赖外不安装其他依赖。后续“计算到回答”的结构化渲染会单独明确输出契约；本阶段不声称修复了 30 题的语义质量。

实际结果：分包、共用计算与 TypeScript 构建完成后，默认/Pi 全量各 **368 passed**；TypeScript typecheck/build/check:generated、Ruff、diff 检查和 wheel 构建/隔离加载均通过。源码/文档入口见 [执行架构说明](../../35-agent-execution-architecture.md)，详细命令与限制见 PROGRESS。此教学阶段完成后暂停；后续相关性上下文、结构化数字渲染和快照优化尚未实施。

## 2026-10-09：Pi 问答可靠性修复（离线实现完成，真实收益待复测）

用户确认按失败复盘继续修改，暂不开启 thinking。本阶段只做离线修复与验证，不新增真实模型执行（原额度仍为 40/40），不切 Pi 默认、不删除旧循环。

1. 将新问题的累计预算/轮数调整为 60,000 tokens/10 轮；显式输入仍可收紧，旧 Turn、重试及 30 题原始记录的 30,000/6 不变。Pi 单轮输入硬上限可配置（默认 16,000），动态分配并为最终输出预留空间。
2. 状态工具直接返回摘要；事实结果明确匹配数、展示数、截断和续查入口。查询沿用一个工具，增加准确字段、多期间、期间类型与分页，支持有限的中英文检索同义词。
3. 从当前 Turn 的成功工具结果生成可恢复工作记录：查询条件、事实/产物定位、计算结果与未完成动作；压缩旧消息时保留，避免重查。完整可见历史仍保留审计，不作为已核实研究结论。
4. 引用错误返回具体错误及本 Turn 可用引用 ID；模型可使用紧凑引用 ID，Python 展开后继续执行原权限/回执校验，历史报告和对外 JSON 不变。
5. 为受限查询/函数提供具体纠正提示，计算结果附输入期间/字段/单位覆盖摘要；不猜单位，不为 30 道题写死流程，不增加第二个 Agent。
6. 从真实失败形态建立离线回归，跑完整测试、Ruff、打包检查，写中文讲解并更新 PROGRESS。真实质量收益仍待另行授权后逐因素复测。

实际离线结果与实现边界见 [可靠性修复说明](../../32-pi-agent-reliability.md)。本阶段完成后暂停；原 30 题质量统计不改写，新增真实执行为 0。

## 2026-10-09：Pi SDK 开放问答迁移（用户已确认，实施中）

本次用户明确批准以下调整，覆盖旧文档中“纯 Python Agent / 暂不引入 Pi runtime”的阶段限制；固定研究方法、账户/交易禁令和报告冻结规则不变。

- 架构：Python 研究核心与 CLI + 项目自带 Node >=22.19 的 Pi 运行进程；锁定 `@earendil-works/pi-ai`、`@earendil-works/pi-agent-core` 0.83.0，不依赖 pi-learning、Pi CLI、OpenCode 或用户全局 Pi 配置。不引入 pi-coding-agent、多 Agent、LangGraph、Web UI。
- 范围：完整迁移 ask、ask-followup、ask-respond、ask-retry、ask-interrupt 与 chat 交互问答路径；保留命令、最终 JSON、Session/Turn/Attempt、不可变报告与显式 Memo。固定研究 Writer 和 Router 保留 Python 实现。
- Pi 接管原生模型/工具循环、消息和事件；Python 继续权威处理工具 schema/授权、来源与公司范围、证据回执、计算、累计预算、状态与事务。Pi turn 是一次模型调用，不等同于用户目标 AgentTurn。首版顺序工具执行。
- 会话：Python 在 data/ 下统一保存规范化用户/可见助手/工具调用 ID、参数、结果摘要及产物引用；不保存隐藏推理、密钥、完整请求正文或完整工具结果。Pi 内存状态从项目记录重建，不另外建立默认 Pi 会话文件。重试不刷新预算，不盲目重放成功工具。
- 历史：来源、正式报告、Memo 与成功回答保持可读且正文不改；旧未完成问题允许重新发起，不承诺跨运行时原位恢复。
- 验收：离线权限、冻结隔离、预算、失败/等待/重试/中断、迁移与 fake-model 回归全部通过。仅对新 Pi Agent 在隔离副本执行现有 30 题各一次，另最多 10 次冒烟/定向复测；累计最多 40 次真实问题执行，每 Turn 沿用默认 30,000 tokens，不自动提高。显式记录执行计数、数值/期间/单位/引用/计算路径、token、延迟、缺口与失败。
- 不以 completed 或平均通过率代替研究验收。可核验的数值、单位、期间、引用与计算路径错误必须修复复测；真实资料缺失并诚实说明可通过。额度耗尽仍有阻塞项时记录限制，不宣称验收通过、不切默认。通过后默认使用 Pi 并移除旧通用循环。
- 交付：代码、必要测试、中文讲解、规格与 PROGRESS 更新；本阶段结束暂停，不顺手实施公司库后续优化或新的产品功能。

实施顺序：保存当前基线 → 项目内 Pi 依赖/受控进程协议 → Python 生命周期与原生工具适配 → 规范化记录/恢复与预算 → 离线回归 → 限额真实题集与定向修复 → 默认切换及清理/教学交接。所有结果以实际运行记录为准。

2026-10-09 实际结果：Pi 预览实现落地；40/40 次真实额度耗尽，30题最后一次质量为4通过/5部分/21失败。质量验收未通过，默认切换和旧loop删除未执行。下一步修复已记录阻塞并经用户另行授权复测，不自动重置额度。详见 [验收记录](../../31-pi-agent-evaluation.md)。

状态：阶段 1–18、阶段 19A 调用轨迹与可选 Phoenix 观测、阶段 19B Skill 驱动的受控工具路由、阶段 19C Turn 级 QueryWorkspace、阶段 19D.1 最小运行时已实现；阶段 16.1 已修复富途嵌套年度字段、年报页级检索和年报 Capex 输入；三家重写草稿均通过当前校验，中国移动的 Q09 状态标签已在可恢复重试中修正。三家公司均保持 draft，等待用户进行真实验收后决定是否冻结。阶段 17 已完成按需 Skill Catalog、Capability Registry、执行审计和 Writer/Agent 上下文加载。阶段 18 已扩展既有 Python Agent loop：来源仲裁、工作记忆、长期记忆索引与受限 `AnalysisWorkspace` 均已落地；不引入 LangGraph、多 Agent 或 Pi runtime。
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

技术细节以 [技术规格](../../technical-spec.md) 为准，研究规则以 [研究规格](../../research-spec.md) 为准。所有首次定义的工程默认值都在规格中明示，不代表用户亲自选择过每个字段。

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
- 教学说明：[docs/11-report-presentation.md](../../11-report-presentation.md)。验证结果记录在 `PROGRESS.md` 阶段 14。

### 阶段 15：Writer 问题对齐与校验失败反馈（已实现，2026-09-27）

- 将 Q01–Q24 的完整问题文本集中定义，并由正式报告渲染器和 DeepSeek Writer 共用，避免模型只看编号猜题意。
- Writer 对每一问增加直接回答题意的要求；特别约束 Q03 必须分析资本开支金额/收入比例/期间/口径，不能用利润率、ROE、净利润或 EPS 替代；Q04 必须分析技术替代/产品变化速度及其竞争影响，不能用 EPS、利润率或估值指标替代。证据不足必须降为 partial/missing。
- 加强 `as_of` 证据边界提示：context 快照不得支撑 answered 或非 null 护城河分；M1 需要单位成本/成本结构优势的直接证据，规模、网络覆盖或牌照本身不等于成本优势。
- 对固定研究的 `validation_failed`，终端显示具体字段位置、错误码、解释和 run_id；尚未通过校验时明确告知没有生成/冻结正式报告。非 TTY `chat --text` 以 `ok=false`、issues 数组和非零退出码返回，保留可解析 JSON。
- 不放宽来源/时间/事实校验，不自动把 context 改成 supports，不自动 finish。已有失败草稿和已完成历史报告保持原状；需要重新生成时由用户决定是否在同一 draft run 上再次调用 DeepSeek。
- Writer 模板版本升级为 `deepseek-draft-v3`，令提示变化反映在 input hash 与 run-step 元数据中。
- 文档：[docs/12-question-alignment-and-validation-feedback.md](../../12-question-alignment-and-validation-feedback.md)。

### 阶段 16：固定研究质量流水线（工程已实现，真实质量待用户验收，2026-09-30）

- 六组 EvidencePack、Provider 定向抽取、PDF/文本确定性排序和证据缺口。
- Decimal 可信计算：净利润同比、资本开支/收入、ROE 序列与波动摘要；模型不能覆盖 Python 结果。
- `write-draft` 默认 staged：六组 Answers、一次综合判断、语义审查、最多 5 个单元各修订一次和最终审查；每次尝试硬上限 15 调用。
- schema v6 增加 `draft_sections` 与 `draft_reviews`；分组按输入哈希恢复，草稿改变使旧审查失效。
- 新 standard run 的 `finish` 需要 matching passing review；完成后冻结 `quality-review.json`。schema v5 旧 run 不受影响。
- 新增 `review-draft`、`--strategy staged|one-shot`，`status` 显示分组、预算、草稿哈希和审查状态。
- 中国移动、联通、电信三份新 run 均覆盖 24 Q、6 Gate、6 Moat，并在阶段 16.1 重写后通过当前自动审查和 `validate`；三份均未 finish，详见 [docs/13-fixed-research-quality.md](../../13-fixed-research-quality.md) 和 `PROGRESS.md`。

### 阶段 16.1：定向证据恢复（实施中，2026-10-01）

- 修复富途 `report_list → item_list` 的期间、币种、会计准则继承，十年 ROE 成为可追溯的可信计算输入。
- 在 schema v7 增加本地 FTS5 页级索引；每题先保留证据，再在固定字符和来源配额内扩展，避免高频词挤掉其他问题。
- 从年报的明确当年 Capex 披露计算 Capex/收入，现金流购买资产仅作为单独标明的 proxy；三家电信均恢复 2021–2025 的比率序列。
- 重写三份草稿并人工核对 Q03、Q04、Q05、Q07、Q09、Q14、Q21、Q23；不自动 `finish`。完成后更新真实结果并暂停。

### 阶段 16.2：固定流程可靠性收口（已完成，2026-10-06）

- 分组 Writer 的 Prompt、JSON Schema、解析、缓存和修订统一为 `AnswerSection` 对象契约；非法 JSON 不再被内部 Statement/Gap 掩盖成空分组。
- 非法 JSON 从原证据输入重新生成一次；精确 G/M ID 映射可无损归一化，其他结构仍由 Pydantic 拒绝。
- DraftReview error 与 Python 硬校验 error 进入同一受限修订循环；不升级 context、不放宽来源边界。
- `chat` 保存失败步骤和恢复动作，同一 session 重试复用原 run、采集资料和成功分组；finish 失败同样保留诊断。
- 腾讯、中国移动、中国电信真实自然语言流程通过 validate；隔离副本的首次及重复 finish、24/6/6 覆盖和产物哈希均通过，原 run 保持 draft。详见 [docs/18-fixed-flow-reliability.md](../../18-fixed-flow-reliability.md)。

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
- `memory-search`、`memory-read` 和扩展后的 `ask-status` 可检查保存内容、Scratchpad、计算与分析审计。详见 [docs/15-open-question-agent.md](../../15-open-question-agent.md)。

### 阶段 19A：调用轨迹与可选本地观测（已完成，2026-10-05）

- schema v9 新增本地 `traces` / `trace_spans`；Writer、Reviewer、Router 和交互 Agent 均记录默认不含正文的调用树。用户可显式设置 `WONDER_TRACE_CAPTURE_CONTENT=preview|full`，仅让本机 OTLP/Phoenix 显示可见 LLM/工具 I/O；项目 SQLite 仍不记录正文，隐藏推理和凭据永不采集。
- `trace-read RUN_ID`、`agent-trace SESSION_ID` 是独立的只读排查入口；`agent_events` 继续是业务审计而非 trace 可视化替代。
- 可选 OpenTelemetry OTLP/HTTP exporter 可以把同一安全 span 发给本地 Phoenix；Agent 额外记录 Skill、iteration、decision 与工具节点，核心流程不依赖 Phoenix、Langfuse 或任何外部宿主。

### 阶段 19B：Skill 选择受控函数（已实现，2026-10-05）

- 新增 `tool_registry.py`，集中管理稳定工具 ID、描述、Pydantic 参数 schema、适用 capability、默认可见状态和 Python handler；没有 handler 的工具不能进入模型 schema。
- `skill.json` 可选声明 `tools`；catalog 拒绝未知/重复 ID、方法 Skill 声明工具和 capability 不兼容的 grants。`analysis-workspace` 通过 manifest 组合财务查询、可信计算、临时数据集、受限代码与结果读取；无需改 Agent loop。
- `chat` 按所选 Skill 生成本次 allowlist，只向开放问题 Agent 发送获准工具的 JSON Schema；执行时以同一 allowlist 二次检查。tool ID、Skill audit、allowlist 和 registry version 保存在 session event/trace。
- `ask-followup` 会重新加载 Skill 并检查 Skill audit、工具集合与 registry 版本；已有固定研究 Skill 元数据和旧 Agent session 可按兼容规则恢复。直接 `ask` 保留原有完整工具集合。
- Fixed Research 流程未改变；Agent loop 的迭代、预算、计算限制、证据引用和正式报告隔离规则未改变。
- 验收：新增工具 Skill、未知/不兼容工具拒绝、Agent prompt 与执行 allowlist 一致、未绑定 handler 的工具不可见、follow-up 检出授权变化；全量离线测试和 Ruff 通过。没有调用真实模型/OpenD/证券账户，也没有修改正式报告。

### 阶段 19C：交互式 QueryWorkspace（已完成，2026-10-06）

本阶段只扩展开放问题 Agent 的受控分析能力，不混入新用户上手、开源包装或评测。固定 `ResearchRun`、评分、门闸、Writer、`validate` 和 `finish` 完全不变。19C 没有重写完整 runtime；它先建立最小的 Turn 数据边界，使 SQL 配额、产物与证据不必在 19D 从 session 语义迁移。

已完成的内部准备（2026-10-06）：既有 `agent.py` 已按不可变策略/动态 prompt、Tool handler、Scratchpad/记忆和主循环拆为小模块，保持 CLI、SQLite、Tool/Skill 授权与输出行为不变。它不实现 Turn、Attempt、SQL 或恢复语义，只降低后续 19C/19D 改动相互缠绕的风险。

**已实现：最小 Turn 契约和 QueryWorkspace。**

- 每次 `ask` 或 `ask-followup` 创建一个不可变的 `AgentTurn`，记录用户输入、ticker/run、Skill/allowlist、provider/model、预算策略和创建时间；`AgentSession` 仍只是长期对话容器，`chat_sessions` 仍只是自然语言入口的确认/恢复状态，三者不能混用。
- schema v10 同时让分析产物和证据回执关联 `session_id + turn_id`。已有 session 按显式迁移补一条 `legacy` turn，只补元数据，不重写事件、报告、Memo 或正式研究内容。完整的 Attempt、重试、恢复命令和模型适配层仍属于 19D。
- SQL、函数和连续失败的资源上限从“每 session”改为“每 Turn”；如需 session 总上限，必须单独、显式记录，重试不得藉此获得新的完整预算。这样后续 Attempt 只消耗同一 Turn 的剩余预算。

- 交互 Agent 的顺序固定为：可信计算优先；不足时在当前 session/ticker/run 的隔离只读财务事实快照上执行单条 `SELECT` / `WITH … SELECT`；必要时才对该 SQL 结果运行受限 Python 函数。文本问题继续使用既有来源搜索和分段阅读。
- schema v10 扩展 `analysis_runs`，并新增实际工具证据的 `agent_evidence_receipts`。每次分析保存在 `data/tmp/analysis/<session>/<turn>/<analysis>/`；保存快照、schema、SQL、可选函数和完整结果，SQLite 只留相对路径、状态、限制、哈希、来源引用和有界摘要。
- 快照仅含结构化 `financial_facts` 和最小 `source_metadata`，不含来源全文、正式报告或 Memo；事实使用稳定 `fact_id`，保留原始 Decimal 文本。快照至多 10,000 条事实，写完改为只读；查询子进程以只读 immutable SQLite 打开。
- Tool Registry/`analysis-workspace` Skill 升级为 v3，新增 `describe_analysis_schema`、`run_readonly_sql`、`run_analysis_function` 和 `read_analysis_artifact`。旧的 JSON 数据集/代码工具仅供旧 session、直接 `ask` 和兼容测试保留，不再暴露给新版 Skill。
- SQL 最长 12,000 字符，最多返回 400 行；独立子进程、SQLite authorizer、`query_only`、progress handler 与既有 10 秒、256 MB、64 KB 输出限制共同拒绝 DDL、写操作、PRAGMA、ATTACH/DETACH、扩展加载、事务和多语句。每个 Turn 最多六次 SQL 尝试、三次函数执行。
- 直接返回 `fact_id` 的行由程序反查真实 `source_id + locator`，才标为 `citable=true`；聚合或未返回原始事实行的 SQL 仅供探索。最终临时报告引用还必须匹配同一 Turn 的证据回执，拒绝编造、跨 Turn 或来源版本不一致的 locator。
- 临时函数只能提交 `def analyze(rows): … return {...}`，只能处理对应 SQL 已存档的结果行。允许局部变量、条件、循环和受控数值/分组函数；禁止 import、属性访问、while、递归、lambda、class、异常处理、文件、网络、SQLite、环境变量、shell 和动态执行。
- SQL/函数的语法、校验、超时和输出错误以结构化可恢复错误反馈给模型；同一错误不得在同一 Turn 原样重复。连续三次分析工具失败才结束该 Turn。查询正文不反复塞入上下文，只保留预览、artifact ID、哈希、行数与来源摘要。
- 验收覆盖 v9→v10 迁移与 legacy turn、快照隔离、SQL/AST 越权拒绝、资源/次数限制、可引用性、函数计算、错误恢复、真实工具回执和端到端 fake-model 路径；19C 专属与 Agent 聚焦离线测试、Ruff 均通过。全量离线 pytest 目前仅余一个既有 Writer 调用预算测试失败，未混入无关 Writer 修改；真实 DeepSeek smoke 未运行，单独记录其限制。实现说明见 [docs/21-query-workspace.md](../../21-query-workspace.md)。

### 阶段 19D：交互 Agent 最小运行时、上手与面试案例

本阶段在 19C 已建立的 Turn 边界上，把已有单 Agent loop 的执行语义补全为可恢复的最小运行时，再把项目整理成可演示、可复现的开源作品。它不复制 Dayu 的完整 Host/EventLog/队列体系，也不引入多 Agent、Pi、LangGraph 或外部宿主。

**19D.1 最小运行时（已实现，2026-10-07）：**

- 明确区分 `AgentSession`（长期对话容器）、`AgentTurn`（一次用户目标和不可变授权/预算快照）与 `AgentAttempt`（一次实际执行或重试）；`ask-followup` 创建新 Turn，不能再以覆盖式 session 结果替代历史回答。
- 为 Turn/Attempt 定义受控状态和合法迁移：Turn 使用 `created → running → waiting_user|succeeded|failed|budget_exhausted|interrupted`，Attempt 保存开始/结束时间、结构化错误和已知消耗；未知用量保留保守预算预留。重试或补充等待中的用户信息都创建新 Attempt，不恢复旧 Python 调用栈，也不重置 Turn 的预算。
- 把 `agent_events`、analysis 产物、证据回执和 trace 关联到 Turn/Attempt；`interactive_reports` 改为按 Turn 保存不可变结果，Memo 保留来源 session 并补充来源 Turn/Report。旧 session、事件和报告以显式 migration 兼容，不能丢失或改写历史内容。
- 明确 CLI 语义：`ask` 创建 Turn，`ask-followup` 创建新的实质问题 Turn；等待用户补充和失败重试必须带目标 Turn ID（例如 `ask-respond`、`ask-retry`），`ask-status` 输出 Turn/Attempt 时间线。相同工具和参数在同一 Attempt 不重复执行；后续 Attempt 只能引用已成功的受控产物，或在上次失败时重新尝试。
- 抽出小型、可替换的 `AgentModelClient` 适配层，提供 scripted/fake model；它只服务交互 Agent，不把 Writer、Router 或项目改造成通用多 provider 框架。循环、工具、等待用户、预算、失败和恢复都能离线测试。
- 复用现有 Scratchpad、Tool Registry 和 trace；不建立通用事件溯源、后台队列、远程 worker、回放外部网络操作或 Dayu 式完整 Host/Engine 分层。
- 验收覆盖旧数据迁移、状态迁移、attempt/报告历史、预算不因重试重置、等待用户恢复、同一工具不重复执行、Skill/allowlist 固化、trace 关联和正式 `ResearchRun` 不变。

教学与实际验证见 [docs/23-agent-runtime-reliability.md](../../23-agent-runtime-reliability.md)。仅完成本小阶段；主库未迁移，记忆/索引优化仍分阶段，19D.2 与 19E 待用户决定。

2026-10-08 按用户最新指令完成额外可靠性收口：借鉴 Pi 的上下文转换和工具结果投影，接通按需富途补取与通用受限计算；原问题真实 DeepSeek smoke 已完成。保留 Python 核心，不引入 Pi runtime。下一步建议优先 19E 小型评测（含实际计算工具调用率），再决定检索/上下文后续优化。详见[通用财务问答](../../25-agent-context-and-financial-tools.md)。

**19D.2 新用户上手、开源可发现性与面试案例（本地面试材料完成，其余待实施）：**

2026-10-11 用户确认并完成本地面试材料：`data/interview/agent-platform-pm/` 下 11 页主讲 + 7 页备份 PPTX/PDF、逐页讲稿、90秒介绍、15道问答和代码/证据索引。按后续要求加入项目整体架构、4个基线Skill及制作期间新增的澄清Skill（开发中未验收），主讲仍约10分钟。使用现有归档，不新增真实调用；质量结果与计划能力明确区分，实际渲染与独立复核见 PROGRESS。本次不精简公开 README、不实施安装体验或社区指标，不表示19D.2整体完成。

- 精简 README 首屏，补英文 Quick Start、功能截图或示例报告、安装/配置排错、项目边界和贡献指南；让首次访问者可在数分钟内跑通演示。
- GitHub Actions 按用户最新要求暂缓；原候选为在受支持 Python 上运行离线 pytest、Ruff 和 demo smoke，不访问网络、OpenD、账户或模型 API。
- 增加面试案例文档，说明目标用户与痛点、为何正式研究采用固定 workflow、为何开放问题采用受控 Agent、为何使用 Python/SQLite 而非先引入 LangGraph、主要失败模式、质量/成本/信任指标，以及明确不做的能力与原因。
- 记录安装成功率、演示完成率、Issue/PR 反馈等可观察信号，再决定是否优先开发更大功能；不开启自动发布或自动部署。

### 阶段 19E：开放问题 Agent 的评测基线

2026-10-08：已准备 [30 道题](../../26-company-data-and-agent-eval.md) 与 [JSON 题集](../../evals/interactive-agent-30.v1.json)，完整题集尚未执行。公司数据层 A、B 已完成：独立公司投影已回填；新 chat 和显式 company_latest 问题按 Turn 固定来源，支持共享查询、补取后索引、本 Turn 证据回执与双库/计算产物备份。直接 ask 的原默认范围不变。详见 [A 实现](../../28-company-data-store.md)、[B 实现与真实冒烟限制](../../29-company-agent-query.md)。下一步 C 执行 30 题，分别检查数值、期间/单位、引用与预算；最新原题冒烟已完成真实函数计算，但单位表述仍未完全合格，不以 completed 当作答案正确。随后 D 按测量优化。完整顺序见 [公司库计划](../../27-company-database-plan.md)，不提前交付阶段 22 语义事实记忆。

- 在 19C 与 19D 的稳定实现之上建立小型、可重复的开放问题评测：答案/计算、引用有效性、工具选择与参数、越权拒绝、预算停止和正式研究隔离分别评分。
- 首版使用 scripted model 和合成测试输入保证离线可重复；真实模型运行作为独立 smoke，报告重复次数、通过率、token、延迟和失败类型，不进入 CI。
- trace 用于定位失败和回归，但不把模型隐藏推理或敏感正文写入评测产物；LLM judge 只有在人工抽样校准后才用于主观完整性评分。
- 19C 与 19D 各自仍必须交付单元、迁移、安全和 fake-model 端到端验收测试；19E 是跨场景的质量/成本基线，而不是把必要测试推迟到之后的理由。
- 本阶段不单独扩展或评测记忆系统；Scratchpad、FTS 和记忆信任边界继续沿用阶段 18。版本化事实记忆及其专项评测仍留在阶段 22；更大规模的公开基准、Pi 和多 Agent 对比留在阶段 21。

### 阶段 20–22：数据覆盖、基准与专业能力（长期）

- 阶段 20 按实际使用需求增加 SEC XBRL、港交所/公司官网数据、专题来源与估值 Skill；先定研究方法、来源边界和验收样例，再增加 capability。OCR 只处理原生提取失败或表格严重损坏的页，并保留原始 PDF 与页码映射。
- 阶段 21 建立公开、可复现的开放问题质量基准，覆盖财务序列、来源引用、时期/币种/口径、计算与证据缺口；先比较当前 Python loop 和 `AnalysisWorkspace`，再按门槛评估多模型或 Pi Agent Core。只有结果显示明确收益才引入。
- 阶段 22 扩展专业 Skill、同行比较、估值附记、HTML/PDF 导出和版本化事实记忆；支持社区贡献的来源适配器与方法 Skill，但权限和业务规则仍由 Python Registry 控制。
- 多 Agent 最多三名受控 worker，须在可并行题目上证明质量提升，或在质量不下降时显著降低延迟。Pi 若采用，只替换开放问题 orchestration/runtime；Python 来源校验、可信计算、SQLite 和正式报告冻结继续权威。

### 面试知识与代码能力学习路线（按需教学，不阻塞实施）

- **19C 前后：** 讲工具调用、受限 SQL/函数、证据回执、提示注入与最小权限；代码重点是 Pydantic 契约、SQLite authorizer、子进程资源限制和故障注入测试。
- **19D 前后：** 讲 workflow 与 Agent 的边界、Session/Turn/Attempt、状态机、幂等、超时、取消与恢复；代码重点是状态迁移、SQLite 事务、适配器协议、`asyncio`/超时语义和 trace correlation。
- **19E 前后：** 讲评测目标、数据集、确定性 scorer、trace grading、LLM-as-a-judge、人工校准、重复试验、成本/延迟和回归阈值；代码重点是 JSONL/fixture、统计汇总、pytest 参数化和可复现报告。
- **阶段 22 前后：** 讲 working/episodic/semantic memory、写入门槛、检索、冲突、失效、版本化与隐私；只有实际基准显示 FTS 召回不足时才讨论 embedding 或 hybrid retrieval。
- 不需要等阶段 19 全部完成才学习。默认在每个相关阶段开始前先做短设计讲解、完成后再按真实代码复盘；用户也可以随时单独要求某一主题的中文课程和小练习。

## 4. 每阶段完成要求

参照 [教学约定](../../teaching-contract.md)。最小实现 → 有意义的测试 → 实际运行 → 中文讲解 → 更新 PROGRESS。阶段 1 不顺手实现全部数据库或数据接口。

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
