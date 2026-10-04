# 项目进度

## 当前状态

- 当前阶段：17 Skill 按需加载已完成。富途年度字段、SQLite 页级索引、年报 Capex 输入和离线验证均已完成；中国移动、联通、电信的新草稿均已通过当前 `validate`。中国移动的 Q09 曾被新状态规则识别为 `answered_with_gap`，已通过可恢复分组重试修正并重新审查。三份均保持 draft，不会自动 `finish`。
- 业务软件：固定研究新增六组 EvidencePack、Decimal 可信计算、staged Writer、定向修订、DraftReview、schema v6 `draft_sections`/`draft_reviews`、schema v7 `source_page_fts`、`review-draft` 和 finish 语义门闸。既有 collect、交互 Agent、Memo、chat 与历史完成报告保持兼容。
- 附记正文与估值：仍未实现。真实 DeepSeek API smoke 已跑通；加入 fundamentals 后 HK.00728 草稿由“1 answered/23 partial”变为“24 answered、6/6 门闸 pass、M1–M6 均有分”。
- writer schema 修复：已加入 `gap` Prompt 约束和一次受控结构修复；修复仍失败保持 `invalid_schema`，不保存草稿、不自动 `finish`。
- 报告层：新完成报告使用 Q01–Q24 完整问题文本、中文标签、精确数值格式和短引用索引；TTY 完成反馈列绝对路径，非 TTY 保持 JSON。
- Writer：默认模板为 `deepseek-staged-v5`；`deepseek-draft-v3` one-shot 仅保留回归。分组 Writer 只接收与该组相关的可信计算，模型提示不替代语义审查，最终 error 不会被自动 finish。
- 自然语言失败反馈：`validation_failed` 在 TTY 显示问题位置、错误码、解释与 run_id；非 TTY 返回 `ok=false`、issues 和 exit 2，不会误报成功。
- Skill：Router 仅接收 Catalog 摘要；`skill-list`、`skill-check` 和 `start --skill` 可用。方法正文/reference 只在确认后的 Writer 或交互 Agent 执行边界加载，实际加载的名称、版本、内容哈希进入 run step 或 Agent event，正文不落库。

## 已准备的交接文件

README.md、AGENTS.md、PLAN.md、docs/research-spec.md、docs/technical-spec.md、docs/teaching-contract.md、docs/provenance.md、docs/09-agent-layer.md、skills/company-analysis-method/、skills/interactive-research-agent/ 和本文件。

## 下一步（阶段 18 前暂停）

用户可查看中国移动 `fe6ecce2…`、中国联通 `e491dee8…` 和中国电信 `e5d20c8…` 的新草稿及结构化审查。重点核对 Q03、Q04、Q05、Q07、Q09、Q14、Q21、Q23 的问题对齐、当期口径和原文 locator；所有 run 仍保持 draft，只有用户决定后才能 `finish`。下一阶段是开放问题的受限 `AnalysisWorkspace`：优先复用可信固定计算，只有现有 capability 不能回答时才允许临时代码；Pi Agent Core 评估仍在阶段 20，当前不迁移技术栈、不引入 LangGraph。

## 阶段 16.1 记录

阶段：16.1，定向证据恢复、页级检索和可信 Capex 输入。

实施日期：2026-10-01。

实现内容：

- schema v7 增加 SQLite FTS5 `source_page_fts`。它从当前 run 已归档的非 Provider 文本构建页或固定行段索引，保存内容哈希；索引只在原文内容变化时更新，不联网，不改变来源文件。
- `EvidencePack` 改成逐题证据配额：每题先保留 2 个片段，再在 18 个片段、每来源 8 个片段、32000 字符限制内扩充至 4 个。`question_chunks` 让 Writer 知道每段证据服务哪个问题。
- 修复富途 `report_list → item_list` 嵌套数据的 `period_text`、`currency_code` 和 `accounting_standards` 继承。三家公司均恢复 2016–2025 的 10 年 ROE 序列。
- ROE、流动比率等比率字段不再继承财务报表货币；ROE 强制使用 `%` 单位且 `currency=null`，防止报告写出“% CNY”。
- 新增年报 Capex 解析：只接受“当年 Capex + 币种 + 数值 + 单位”的明确披露，优先详细的 `for the year` 百万元数字；摘要的四舍五入数字只有与详细披露误差不超过 0.5% 时才视为同一指标。现金流购买固定/无形资产仍单独标成 `cash_capex_proxy`。
- 中国移动、联通和电信均恢复 2021–2025 年报 Capex/收入比。年报页级索引条目分别为 1,044、1,220、1,570；中国电信 2021–2025 Capex 为 867.23、925.28、988.38、935.13、803.64 亿元人民币，均可回到对应年报页。
- Writer 的引用校验纳入随 Prompt 输入的可信计算原始输入，因此模型可引用实际的 Capex/ROE 页码或 Provider locator，仍不能引用未输入来源。并按问题组裁剪可信计算，避免无关十年序列使小组 Prompt 膨胀。

验证：

- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest python -m pytest -q`：135 passed（2026-10-03，包含 `answered_with_gap` 与结构化数值审查误报回归）。
- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with ruff ruff check src tests`：All checks passed。
- 离线对三份现有 run 执行页级索引、EvidencePack 和可信计算复核：三家 ROE 均 10 年，Capex/收入均 5 年；未改写已完成历史报告。
- 中国移动最终恢复草稿为 `deepseek-staged-v5` 的 6 次调用、311254 tokens、1 个修订单元，审查为 0 error / 20 warning，`validate` 通过。新增的 `answered_with_gap` 规则先识别出 Q09 的旧状态标签错误；经历 `ProxyError`、`ChunkedEncodingError` 与空组响应后，缓存式重试补齐所有分组、重新保存草稿并完成审查。
- 中国联通 `e491dee8…` 已重写为 0 error / 20 warning，`validate` 通过；最后成功尝试为 11 calls、389702 tokens、0 个修订单元。中国电信 `e5d20c8…` 已重写为 0 error / 20 warning，`validate` 通过；最后成功尝试为 8 calls、371399 tokens、4 个修订单元。两份均保持 draft。
- 审查 Prompt 现明确 `NumericFact` 的逐项数值和来源就是可复核正文；在明确携带 `supports` 引用时，模型不能仅因自然语言句子未重复计算输入或 PDF 使用 `page N` 而报 error。Python 仅对这些可从结构验证的误报降级。
- `validate_packet` 现在拒绝 `answered_with_gap`，`derive_quality_status` 也会将它标为 `evidence_incomplete`；缓存复用同样执行此规则。

## 阶段 17 记录

阶段：17，Skill 按需加载与 Capability Registry。

完成日期：2026-10-04。

实现内容：

- 新增 `capabilities.py`：`fixed_research`、`interactive_research` 和预留但不可用的 `analysis_workspace` 均有 Pydantic 输入/输出、权限说明和审计目标。Skill Markdown 不能创建新 capability 或副作用。
- 重写 `skill_registry.py` 为 Catalog 与按需加载两层：Router 只能取得 name、description、path、kind、适用 capability 和可用性摘要；`load_for_capability` 在执行边界才读取 `SKILL.md` 与目录内 reference，并对内容哈希做一致性校验。
- 新增可选 `skill.json`。没有 manifest 的目录为纯方法 Skill；能力 Skill 必须声明已注册 capability。拒绝缺 description、资源越界/不存在、重名、依赖循环、重复依赖/capability、未知或尚不可用 capability、重复选择与 capability 不匹配。
- `RoutePlan` 新增默认空数组 `skill_names`，旧 Router JSON 保持兼容。`start --skill`、`skill-list`、`skill-check [NAME]` 已实现；现有 `company-research` 和 `interactive-research-agent` 迁移为能力 Skill，均依赖 `company-analysis-method` 方法 Skill。
- `start` 和固定 `chat` 会把加载的名称、版本、内容哈希、类型和 capability 写入 `run_steps.skill_selection`。`write-draft` 从该审计记录重新加载内容，纳入 Prompt 与输入哈希；内容改变时返回 `skill_changed_since_selection`，不混用旧 run。
- 交互 `chat` 在确认后把选中方法正文/reference 加入受限 Agent 上下文；Agent event 只记录摘要。`ask-followup` 重新加载并核对同一哈希，内容改变时停止会话。Python 工具白名单、来源引用验证、ResearchRun 状态和 finish 确认仍为权威控制。
- 新增教学文档 `docs/14-skill-on-demand.md`，同步更新 README、PLAN、自然语言入口和技术规格。

验证：

- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest python -m pytest -q`：143 passed（2026-10-04）。新增覆盖 Router 摘要不泄露正文/reference、纯方法即插即用、内容哈希、非法 manifest/路径/循环/能力、旧 RoutePlan、固定 Writer 重载和交互 Agent 上下文；既有全量回归同时通过。
- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with ruff ruff check src tests`：All checks passed。
- 尚未调用真实 DeepSeek，也没有新建真实 ResearchRun 或执行 finish；三家电信 draft 及已完成历史报告未改动。

已知限制：阶段 17 不提供临时代码、shell、网络或新数据能力。`analysis_workspace` 仅注册为未来 capability；阶段 18 才实现只读数据导出、临时目录、资源上限、代码/输入哈希和结果审计。

## 阶段 16 记录

阶段：16，固定研究质量、证据包、可信计算、语义审查与定向修订。

完成日期：2026-09-30（工程与真实运行完成；质量结果待用户验收）。

实现内容：

- 新增 `fixed_quality.py`：六组 `EvidencePack`、有界 `EvidenceChunk`、Provider JSON 节点抽取、PDF/文本页行切块、来源资格、内容哈希、截断与 gap。
- 新增 Decimal 可信计算：净利润同比、资本开支/收入、ROE 序列、覆盖年数、超过 15% 年份数、min/max/median/总体标准差，并保留公式版本、输入来源、期间、币种、缺失与不可比原因。
- `write-draft` 默认 `staged`：六组 Answers、综合判断、初审、最多 5 个单元各修订一次、最终审查；单次尝试调用硬上限 14。`one-shot` 保留回归但新真实 run 不能绕过审查。
- 分组答案和综合判断的每条引用必须来自实际传入的 EvidencePack；`support_eligible=false` 的片段不能写为 `supports`。缓存分组在复用前也会重验这一边界；越界时只重写受影响分组。Reviewer JSON 无法解析时会按紧凑契约重试一次。
- schema v6 增加 `draft_sections`/`draft_reviews`。成功分组按 input hash 恢复；草稿改变使旧 review stale；新 standard run 缺 passing review 时 finish 必然失败；v5 历史 run 不受影响。
- 新增 `review-draft` 与 `DraftReview`；拒绝漏审、旧 hash、非法引用和虚假 passed。finish 通过后会冻结 `quality-review.json` 并纳入哈希。
- `status` 返回分组缓存、14 次调用预算、draft hash、review 状态与问题计数。chat 继续复用 `validate`，因此语义失败不会进入 awaiting_finish。
- 修正文档漂移：one-shot 的实际上限为非 Provider 每来源 4000 字符、Provider 每来源 120000 字符、总计 300000 字符；中国电信旧 run `9f0fd795…` 的 v3 草稿实际已重写并在阶段 16 前可通过旧 `validate`，不再描述为“等待重试”。

阶段 16 初始真实基线与验收（已由后续 16.1 恢复结果替代）：

- 一次性 Writer 已记录基线：中国移动 completed run `f0ae5ab9…` 为 139371 tokens；中国电信 v3 draft run `9f0fd795…` 为 142880 tokens。旧 metadata 未记录完整调用耗时，不能补造。
- 三个新 schema v6 run 各复用阶段 13 的 20 条归档来源，均得到 24 Q、6 Gate、6 Moat，均未 finish。
- 这三份 run 的初始审查结果已不再代表当前草稿。阶段 16.1 用修复后的富途字段、页级检索、年报 Capex、状态语义和审查规则重新生成草稿；当前结果、调用统计与验收状态记录在本文件前面的“阶段 16.1 记录”和 `docs/13-fixed-research-quality.md`。
- 三个 run 的确定性结构、归档和来源校验持续通过；先前发现的 context 误作 supports 已被 EvidencePack 边界拦截并触发局部重写。三家当前均通过自动门闸，仍需要人工判断 warning，尤其是 locator、跨期/跨口径、单位/币种与跨章节表述。

验证命令与结果：

- `./.venv/bin/python -m pytest -q`：125 passed（阶段 16 当时结果；当前全量结果见阶段 16.1 的 135 passed）。
- `./.venv/bin/ruff check .`：All checks passed。
- `write-draft ... --strategy staged`：三家公司真实 DeepSeek 运行，均生成完整结构并执行审查；没有调用 `finish`。
- schema v5 → v6 实际迁移成功；已有 completed run 的 schema_version 与产物未改写。

新增/修改文件：`src/wonder_stockresearch/fixed_quality.py`、`models.py`、`storage.py`、`research.py`、`writer.py`、`cli.py`、`tests/test_stage16_quality.py` 及相关回归测试、`docs/13-fixed-research-quality.md`、README、PLAN、技术规格和本文件。

已知限制：三家均通过自动门闸，但 warning 仍需用户人工审阅后才应 finish。Reviewer token 较高且仍会误报，Python 只降级可确定识别的自相矛盾，不替模型放行实质问题。当前证据检索为关键词排序，不是语义检索。阶段 17 已完成；阶段 18 的临时计算环境尚未开始。

## 阶段 15 记录

阶段：15，Writer 问题对齐与校验失败反馈。

完成日期：2026-09-27。

实查的中国电信失败 run：`9f0fd795-4e44-4ec4-b523-fd4a71412233`，关联 chat session `43f5c00f-bd7b-451d-ab85-8cc50408dd8b`。`validate` 返回两项 `missing_authoritative_support`：`draft.answers[0].statements[0]`（Q01）及 `draft.moats[0].rationale`（M1）。两处都引用了 Futu `profile` snapshot `3d4c3a4a-101f-4261-8725-36e0c1ad68e8`，其 `observed_after_as_of=true`，模型将引用关系标成 `context`；规则正确阻止其支撑已回答/非空评分。此次 validate 是只读核验。

同一未完成草稿的 Q03 实际写了经营/投资/融资现金流，Q04 写了资产负债表与负债率，均未回答对应问题。这进一步确认原 writer prompt 只列 Q 编号而未给问题定义，是题意错位的直接诱因。

实现内容：

- 新增 `questions.py`，集中维护完整 Q01–Q24 文本；正式报告渲染器和 Writer 共用，避免两份问题定义漂移。
- Writer prompt 纳入全部问题全文，增加 Q03 资本开支、Q04 技术替代/产品变化专项要求及回答前自查；补充 Q01/M1 的直接证据要求与 context/as_of 边界，不把 context 自动提升为 supports。
- 模板版本升级为 `deepseek-draft-v3`。
- 固定 `chat` 现在展示 `validation_failed` 的问题位置、错误码、说明、run_id 和未定稿提示；非 TTY 使用 `ok=false` JSON、逐项 issues 和 exit code 2。
- 不修改历史 completed 报告，也未覆盖当前 run 的草稿；未调用 DeepSeek。修正提示仍需实际模型重跑才能验证语义效果。

新增/修改文件：`src/wonder_stockresearch/questions.py`、`src/wonder_stockresearch/reports.py`、`src/wonder_stockresearch/writer.py`、`src/wonder_stockresearch/chat.py`、`src/wonder_stockresearch/cli.py`、`tests/test_writer.py`、`tests/test_chat.py`、`docs/07-deepseek-writer.md`、`docs/10-standalone-natural-language.md`、`docs/12-question-alignment-and-validation-feedback.md`、`docs/technical-spec.md`、`PLAN.md`、`README.md` 和本文件。

验证命令与实测结果：

- `./.venv/bin/python -m pytest -q tests/test_writer.py tests/test_chat.py tests/test_reports.py`：34 passed。
- `./.venv/bin/python -m pytest -q`：110 passed。
- `./.venv/bin/ruff check .`：All checks passed。
- `git diff --check`：通过，无空白错误。
- `./.venv/bin/wonder-research --help`：CLI 冒烟通过。
- Writer prompt 回归测试确认完整 Q03/Q04 题目、不可替代指标和 M1 直接证据规则；chat 测试确认 TTY 显示校验错误详情，非 TTY 输出 JSON 错误并返回 exit 2。
- 当前失败 run 仍为 `draft`，更新时间未变；没有调用 DeepSeek 或覆盖草稿。

如需让这条 run 使用新提示重写，可执行 `uv run wonder-research --root . write-draft 9f0fd795-4e44-4ec4-b523-fd4a71412233 --provider deepseek`。该命令会调用 DeepSeek 并替换该未完成 run 中的草稿，实施时因此保留原样，等待用户决定。

## 阶段 14 记录

阶段：14，定稿反馈与正式报告可读性。

完成日期：2026-09-27。

实现内容：

- TTY 下的 `finish` 和自然语言 `chat` 正式研究完成时显示状态、质量、护城河分数、run 目录和正式产物绝对路径；复评存在时列出 `review.md`。完成摘要不重复 session_id，不倾倒原始 JSON。
- 非 TTY `finish` 保持现有 JSON 输出契约。幂等完成返回不含派生字段时，CLI 只读查询 run 状态补齐。
- 后续 `company.md` 显示研究规格中的完整 24 问，枚举状态使用中文；来源引用采用稳定短编号和完整来源索引。
- 重复的相同引用及陈述与数值之间完全相同的引用只显示一次；不同来源定位或关系保留。Decimal 数值添加千位分隔，保留全部小数位，不换币种、不舍入，单位与币种相同则不重复。
- 不修改数据模型、数据库 schema、评分、Writer Prompt 或已完成报告。本阶段没有调用 DeepSeek，也没有重写现存 `data/` 产物。

新增/修改文件：`src/wonder_stockresearch/reports.py`、`src/wonder_stockresearch/cli.py`、`tests/test_reports.py`、`tests/test_storage.py`、`tests/test_chat.py`、`docs/11-report-presentation.md`、`docs/technical-spec.md`、`PLAN.md`、`README.md` 和本文件。

验证命令与实测结果：

- `./.venv/bin/python -m pytest -q`：106 passed。
- `./.venv/bin/ruff check .`：All checks passed。
- `git diff --check`：通过，无空白错误。
- `./.venv/bin/wonder-research --help`：CLI 冒烟通过，命令列表正常输出。
- 定向测试覆盖 TTY `finish`/`chat` 摘要、stdout 非 TTY 保持 JSON、存在/不存在 `review.md`、缺分数、完成重试、路径带空格、24 问文本、引用映射/去重和 Decimal 精度。
- 最近已完成的中国移动样例 `company.md` 与 `research.json` 在实施前后 SHA-256 未变化：`4dee46839176b133f7884b6be6d9c2da9ab034d1321fb65faa8f52824e1bd3fd`、`59a9ad237697fefeaf995624748f923957c97bc83be4fa9c648da70c9d99a859`。
- 未调用 DeepSeek；现存报告、研究 JSON 和评分均未更改。

已知内容限制：样例中 Q03/Q04 的回答与题意是否匹配属于研究质量问题，未由排版阶段处理。下一步仍待用户决定是否使用三家公司语料验证 DeepSeek 草稿质量。

## 阶段 13 记录

阶段：13，三家电信公司数据语料与覆盖核验。

完成日期：2026-09-26。

实现与核验范围：仅使用本项目 Python CLI、HKEX 公开公告和 Futu `OpenQuoteContext`；没有调用账户、持仓、资金、订单或交易 API，没有写入正式研究报告或触发 DeepSeek 写作。

干净验证 run：

| 公司 | 代码 | run_id | 年报/中期报告 | 结构化财务 |
| --- | --- | --- | --- | --- |
| 中国移动 | HK.00941 | `a9ba08c2-28dc-492e-9d66-8b35625202b0` | FY2021–FY2025；2024H1、2025H1、2026H1 | 三表 + 关键指标，2016/FY–2025/FY，CNY，国际会计准则 |
| 中国联通 | HK.00762 | `f1041b11-fed9-43fe-8700-8410d903be19` | FY2021–FY2025；2024H1、2025H1、2026H1 | 三表 + 关键指标，2016/FY–2025/FY，CNY，香港会计准则 |
| 中国电信 | HK.00728 | `45d78782-fafa-473a-83ae-41c8997b7f75` | FY2021–FY2025；2024H1、2025H1、2026H1 | 三表 + 关键指标，2016/FY–2025/FY，CNY，国际会计准则 |

每个验证 run 均有 20 条来源：8 份定期报告 PDF、1 份 HKEX 公告索引、11 个基本面数据集；年报和中期报告的 PDF 文本提取状态全部为 `available`。三表的四类报表分别含 10 个连续年度期，主营构成另含 10 个可用年度期与最近 3 个中期；主营构成 `history_failures` 在最终干净语料中均为空。Futu 的历史主营构成年份不保证连续：移动为 2015、2016、2018–2025；联通为 2016–2025；电信为 2014–2016、2019–2025。不得把其缺失年份补值或误称为连续十年。

实际运行与结果：

- `collect <run_id> --kind filings`：三家公司各选中并下载 8 份报告，下载失败 0；分类器回归测试确保标题只是提到“2021 Annual Report”的董事会公告不会被误认成年报。
- `collect <run_id> --kind fundamentals`：三家公司各 5 个采集子步骤成功，provider 返回 0 个失败；财务原始期间、币种、会计口径保留在来源 JSON。
- `./.venv/bin/python -m pytest -q tests/test_collect.py tests/test_fundamentals.py`：18 passed。
- `./.venv/bin/python -m pytest -q`：100 passed；`./.venv/bin/ruff check .`：All checks passed；`git diff --check`：无空白错误。
- 对来源 JSON 逐家审计：年度财务期间为 2016–2025；年度/中期 PDF 均可提取；主营构成最终 `history_failures=[]`。
- 本地资料写入 `data/`，由 `.gitignore` 忽略；没有把数据库、原文或报告加入 Git。

本阶段发现与处理：首次下载中国移动时，旧标题分类器把标题中提及 2021 年报的董事会公告误选为年报。收紧标题前缀识别并补回归测试后，重新建立干净 run；原先 run `0dce5a5c-78c3-479c-bd2e-0116cfd41d78` 保留作历史记录，不作为验证语料。中国电信第一次基本面采集遇到 Futu 每 30 秒调用限额，随后重试确认接口可用，并重新建立干净 run `45d78782-fafa-473a-83ae-41c8997b7f75`，最终无历史拆分失败。

另有一个误用未来 45 分钟 `as_of` 参数创建的空草稿 run `b712c0a3-823e-438b-8910-037e07296519`；未关联来源、未作为验证语料，保留原状，不要继续使用。正确的中国电信验证 run 是上表中的 `45d78782-fafa-473a-83ae-41c8997b7f75`。

修改文件：`src/wonder_stockresearch/disclosures.py`、`src/wonder_stockresearch/futu_fundamentals.py`、`tests/test_collect.py`、`tests/test_fundamentals.py`、`PLAN.md`、`AGENTS.md`、`README.md`、`docs/04-data-connectors.md`、`docs/05-opencode-and-tools.md`、`docs/08-futu-data-capabilities.md`、`docs/technical-spec.md` 和本文件。

已知限制：主营拆分按 Futu 实际可用期间保存，不保证每只股票都有连续十年；Futu 未提供 item 级单位，不推断单位。资料是本地验证语料，不代表模型结论正确，也未验证草稿通过率。OpenCode 仅留作历史集成记录，后续不需要任何外部宿主。

## 阶段 7.2 记录

阶段：7.2，DeepSeek `ResearchDraft` schema 修复。

完成日期：2026-09-22。

实现内容：

- 加固 writer Prompt，明确 `Answer.gap` 只能为 `null` 或完整四字段对象，并覆盖 `answered`、`partial`、`missing` 三种状态示例。
- 初次模型输出仅在 Pydantic schema 校验失败时触发一次结构修复；修复请求不得改变事实、数字、引用、ticker 或 as_of，Python 会拒绝非 `gap` 字段变化。
- `run_steps.metadata_json` 记录模型调用次数、修复状态和初次/最终校验结果，不保存原始模型输出、完整提示词或密钥。
- 修复失败继续返回 `invalid_schema`，不保存未经校验的草稿，也不改变显式 `finish` 边界。

实际验证：

- `PYTHONPATH=src:. uv run --no-project --with pytest --with pydantic --with requests python -m pytest -q tests/test_writer.py`：16 passed。
- `PYTHONPATH=src:. uv run --no-project --with ruff ruff check src/wonder_stockresearch/writer.py tests/test_writer.py`：All checks passed。

已知限制：本次只修复结构校验失败，不自动修复非法 JSON、网络错误或事实/引用错误；真实保利物业 run 需要在用户配置 DeepSeek API 后重新执行 `write-draft` 验证模型实际修复率。

## 阶段 11–12 记录

阶段：11–12，进度事件与独立自然语言入口。

完成日期：2026-09-22。

新增/修改文件：`src/wonder_stockresearch/progress.py`、`src/wonder_stockresearch/router.py`、`src/wonder_stockresearch/skill_registry.py`、`src/wonder_stockresearch/chat.py`、`src/wonder_stockresearch/models.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/research.py`、`src/wonder_stockresearch/writer.py`、`src/wonder_stockresearch/agent.py`、`src/wonder_stockresearch/cli.py`、`src/wonder_stockresearch/symbols.py`、`tests/test_chat.py`、`tests/test_symbols.py`、`README.md`、`AGENTS.md`、`PLAN.md`、`docs/09-agent-layer.md`、`docs/10-standalone-natural-language.md`、`docs/technical-spec.md`、`skills/company-research/SKILL.md` 和本文件。

实现内容：

- `ProgressEvent` 和 stderr 进度输出：`collect`、`write-draft`、`ask`、`chat` 可以报告简短阶段状态，最终 stdout JSON 契约不变；相关命令支持 `--quiet`。
- Skill Registry 独立扫描项目 `skills/*/SKILL.md`，为自然语言 Router 加载 Skill 上下文；Skill 不构成权限，Python 业务服务仍控制所有副作用。
- 新增 `RoutePlan` 和 DeepSeek Router，支持正式研究、开放问题、Memo/澄清等意图；路由不直接执行数据库、网络或报告写入。
- 新增 `chat`：支持 `chat` 连续对话和 `chat --text` 单次输入；非严格自然语言会先生成候选计划并请求确认。
- 正式研究自然语言流程自动执行到 `validate`，通过后等待第二次确认才 `finish`；全部采集阻塞或校验失败时停止并返回原因。
- schema v5 新增 `chat_sessions`，保存待确认动作、对话历史、run_id 和交互 Agent session 关联，支持重启后继续。
- 当前未新增宏观、产能、库存周期或同行业专用 Agent；TypeScript runtime、LangGraph 仍未引入。

实际运行命令与结果：

- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest --with pydantic --with requests --with ruff python -m pytest -q`：93 passed。
- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with ruff ruff check .`：All checks passed!。
- `wonder-research --help` 和 `wonder-research chat --help`：均退出码 0，显示自然语言入口和参数。
- 真实 chat 路由 smoke：`uv run wonder-research chat --text "帮我研究一下中国联通"` 成功识别为 `fixed_research`、`HK.00762`，返回 `awaiting_confirmation`；未自动采集、写草稿或 finish。
- CLI 交互修复：终端直接使用 `chat --text` 时，遇到确认/追问状态会继续复用当前 session；非交互 stdin 仍保持一次性 JSON 输出。设计参考 Dexter 的会话式 Agent loop，但不引入其 TypeScript/Bun runtime。
- 简称标的消歧：新增 `SymbolCandidate`、富途 `get_search_quote` 只读搜索、本地清单匹配、模型未验证候选和 `awaiting_symbol_selection`；候选选择及确认前不创建 ResearchRun。
- 真实“保利” smoke：`chat --text "帮我研究一下保利"` 返回两个已由富途验证的候选——`HK.06049 保利物业`、`HK.00119 保利置业集团`；输入 `1` 后进入 `awaiting_confirmation`，未启动采集。

已知限制：自然语言 Router 和模型候选兜底仍需要 DeepSeek API；富途候选搜索依赖本机 OpenD；chat 目前不直接完成 Memo 内容提取，Memo 仍建议使用显式 `memo-save`。

修复记录：DeepSeek 对没有专项分析方向的正式研究可能返回 `focus: null`；路由模型已将其规范化为 `general`，并增加中国联通真实返回形状的回归测试。

## 阶段 8–10 记录

阶段：8–10，独立交互 Agent、临时报告与公司 Memo。

完成日期：2026-09-22。

新增/修改文件：`src/wonder_stockresearch/agent.py`、`src/wonder_stockresearch/models.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/cli.py`、`tests/test_agent.py`、`skills/company-analysis-method/SKILL.md`、`skills/interactive-research-agent/SKILL.md`、`skills/company-research/SKILL.md`、`docs/09-agent-layer.md`、`docs/technical-spec.md`、`docs/research-spec.md`、`PLAN.md`、`README.md`、`AGENTS.md` 和本文件。

实现内容：

- 固定研究流程继续使用 `start → collect → source-read → write-draft/save-draft → validate → finish`，交互 Agent 不修改正式 `ResearchRun`。
- 新增 schema v4：`agent_sessions`、`agent_events`、`interactive_reports`、`company_memos`，从旧 v1/v2/v3 数据库自动迁移。
- 新增独立 CLI：`ask`、`ask-followup`、`ask-status`、`memo-save`、`memo-list`、`memo-read`。
- `InteractiveQuestion.focus` 已支持 `challenge`、`macro`、`peer`、`capacity_cycle`、`inventory_cycle` 五种受控分析角色；它们仍是同一个 Agent，不自动联网或拆分子 Agent。
- Agent 直接调用 DeepSeek API，默认只读取正式报告、历史、已归档来源和 Memo；工具参数、source_id 和 locator 由 Python 校验。
- `memo-save` 是唯一永久保存交互内容的入口；同标题 Memo 追加版本，不静默覆盖。
- `allow_network=true` 当前明确拒绝，联网采集仍须先走固定 `collect`/`source-add`。
- `AGENTS.md` 和 `docs/09-agent-layer.md` 明确要求后续 Agent 实现参考用户指定的 Dexter 项目，但不复制其 TypeScript/Bun 技术栈，不把 OpenCode 作为运行前提。

实际运行命令与结果：

- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest --with pydantic --with requests python -m pytest -q`：81 passed。
- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with ruff ruff check src tests`：All checks passed!。
- fake DeepSeek Agent 测试覆盖临时报告、正式 run 不变、Memo 显式保存与版本递增、虚假 source_id 拒绝、CLI JSON 契约。

已知限制：交互 Agent 当前不自动联网、不执行 collect/source-add、不提供宏观/同行/产能周期专用数据工具；这些属于后续阶段。真实交互 Agent API smoke 尚未在本次实现中重复运行，用户需在运行环境配置自己的 `DEEPSEEK_API_KEY` 后执行。

## 阶段 7.1 记录

阶段：7.1，富途基本面变量接入（用户 2026-09-21 决定，as_of“方案 4”）。

完成日期：2026-09-21。

新增/修改文件：`src/wonder_stockresearch/futu_fundamentals.py`、`src/wonder_stockresearch/research.py`、`src/wonder_stockresearch/writer.py`、`src/wonder_stockresearch/cli.py`、`tests/test_fundamentals.py`、`docs/08-futu-data-capabilities.md`、`docs/research-spec.md`、`docs/technical-spec.md`、`README.md` 和本文件。

实际运行命令与结果：

- `uv run pytest -q`：77 passed（阶段 1–7 的 72 个测试保持通过，新增 5 个 fundamentals 测试）；`uv run ruff check .`：All checks passed!。
- 真实 `collect --kind all`（HK.00728）：8 个子任务全部 succeeded——`market_snapshot`、`financial_statements`、`fundamentals_company/financials/revenue/capital/valuation`、`filings`；归档 11 个基本面数据集（profile/executives/plates/shareholders/statements/operational_efficiency/revenue_breakdown/dividends/buybacks/valuation/consensus）。
- 真实 `write-draft`（同一草稿，thinking=disabled、temperature=0.2）：改造前“1 answered / 23 partial / 6 门闸 insufficient / 护城河全 null” → 改造后“24 answered / 6 门闸 pass / M1–M6 均有分”；`usage≈128k+12k` tokens。剩余 1 处 `missing_authoritative_support`（Q02 一条陈述未给 supports），由校验正确拦下，未 finish。

与计划的差异及原因：无产品范围差异。关键决策：
- as_of **方案 4**：provider 数据集带 `available_from`（报告期末/披露日）且不晚于 as_of 的**已报告数据**可支撑核心结论；`evidence_class=snapshot`（行情/估值/评级/板块）仅 context。实时数据不影响公司判断。
- `collect` 新增 `--kind fundamentals`，`all` 包含它；每个数据集一个 run_step，原始 JSON 全量归档，metadata 记 `dataset/evidence_class/available_from/currency/accounting_standard/observed_at`。
- writer 上下文：provider 结构化 JSON 额度提高到 12 万字符/来源、总量 30 万字符；上下文显式列出“可作 supports 的 source_id”与“仅 context 的 source_id”，并提示 numbers 必须带 `evidence` 数组（修复模型把 evidence 写成非数组的 `invalid_schema`）。

已知限制：定性问题（技术替代、份额趋势、提价能力、品牌、认错纠错、客户价值/渠道/留存/必需性、破坏情景）仍缺；模型引用纪律是概率性的，可能出现 `missing_authoritative_support`，需重跑或人工修正；依赖本机 OpenD 与相应行情权限。

## 阶段 7 记录

阶段：7，DeepSeek 直接写作入口（含 run_steps v2→v3 迁移）。

完成日期：2026-09-21。

新增/修改文件：`src/wonder_stockresearch/writer.py`、`src/wonder_stockresearch/envfile.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/research.py`、`src/wonder_stockresearch/cli.py`、`.gitignore`、`.env.example`、`tests/test_writer.py`、`tests/test_envfile.py`、`tests/test_run_steps.py`、`docs/07-deepseek-writer.md`、`README.md`、`PLAN.md`、`AGENTS.md`、`docs/technical-spec.md` 和本文件。

实际运行命令与结果：

- `uv run pytest -q`：72 passed（阶段 1–6 的 56 个测试保持通过，新增 11 个 writer 测试、4 个 `.env` 加载测试与 1 个 v2→v3 迁移测试）。
- 项目根 `.env`：`cp .env.example .env` 后 `git check-ignore .env` 命中、`git status` 不显示 `.env`；`envfile.load_env_file` 解析出 1 条（不打印值）。
- 真实 DeepSeek API smoke（用户本机 `.env` 配置 key 后）：对 HK.00728 含 5 条来源的 draft run 执行 `write-draft` → exit 0、`saved=true`、`issues=[]`；`usage={prompt 6337, completion 9534, total 15871}`；随后 `validate` exit 0、24 问、`evidence_incomplete`。
- 首轮失败的修正：默认 thinking 开启使推理 token 挤占 `max_tokens` 导致 `invalid_json`（截断）、以及模型数字缺 `period` 导致 `invalid_schema`。改为默认 `thinking=disabled` + `temperature=0.2`（低温度、结构化更稳）并在 prompt 明确 numbers 必填字段，问题消失；参数可由 `--thinking`/`--temperature` 或 `WONDER_DEEPSEEK_THINKING`/`WONDER_DEEPSEEK_TEMPERATURE` 覆盖。
- `uv run ruff check .`：All checks passed!。
- `uv run wonder-research --help`：退出码 0，新增 `write-draft`。
- 迁移 smoke（项目真实 DB）：`user_version` 由 2 → 3，`run_steps` 出现 `metadata_json` 列，旧数据保留。
- 缺 key smoke：`write-draft` 在未设置 `DEEPSEEK_API_KEY` 时 exit 2、issues=[`missing_api_key`]，不发起请求、不写 `run_steps`，run 仍为 draft。
- 官方文档核对（2026-09-21）：base `https://api.deepseek.com`、`/chat/completions`、`response_format={"type":"json_object"}`，模型 `deepseek-flash`/`deepseek-v4-pro`。
- fake HTTP 测试覆盖：成功、超时/网络失败、HTTP 500/401、非法 JSON、schema 不符（含数字缺 period）、空内容、伪造 source_id、缺 key；payload 断言默认 `thinking=disabled` 与 `temperature=0.2`，且不含 key。

与计划的差异及原因：无产品范围差异。工程默认值并记录：默认模型 `deepseek-flash`（可 `--model`/`WONDER_DEEPSEEK_MODEL` 覆盖），`max_tokens` 默认 16000（`WONDER_DEEPSEEK_MAX_TOKENS`），读取超时 300s；上下文最多 20 条来源、每条 ≤4000 字符、总计 ≤24000 字符；`run_steps` 用 `metadata_json` 记 `template_version`/`usage`/`finish_reason`/issue 数，不含提示词与凭据。

已知限制：单供应商；仅对 schema 错误做一次结构修复，不做事实重写、工具循环或自动检索；不自动 finish；真实 API 质量与限额未验证；附记/估值未实现。

## 阶段 6 记录

阶段：6，手动复评、备份与维护。

完成日期：2026-09-21。

新增/修改文件：`src/wonder_stockresearch/research.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/reports.py`、`src/wonder_stockresearch/cli.py`、`tests/test_review_and_backup.py`、`docs/06-review-and-maintenance.md`、`README.md`、`PLAN.md`、`docs/technical-spec.md` 和本文件。

实际运行命令与结果：

- `uv run pytest -q`：56 passed（阶段 1–5 的 47 个测试保持通过，新增 6 个阶段 6 测试与 3 个 as_of 可用性收口测试）。
- `uv run ruff check .`：All checks passed!。
- `uv run wonder-research --help`：退出码 0，新增 `compare`、`backup`。
- 复核修复的核心证据 as_of 可用性：provider 现在必须同时满足 `metadata.observed_at` 存在且可解析、不晚于 `draft.as_of`、且 `observed_after_as_of` 不为 true；只有日期的 primary 以当地日终为保守可用时点。未知/晚于 as_of/缺 observed_at 的来源仅作 context，仍归档保留。
- 真实 CLI smoke（项目根，基线为阶段 5 的 HK.00728 completed run `2d441bba…`）：`start HK.00728 --baseline` → `save-draft`（source_count=7，issues=[]）→ `validate`（exit 0）→ `compare`（comparable=true、score_total.diff=null、changes 全 0、new_sources=[]、reused=7）→ `finish`（artifacts=[company.md, research.json, review.md]）→ `review.md` 显示“差值：不适用”。
- 备份 smoke：`backup --output data/backups/stage6-smoke` → file_count=19、missing=[]、manifest_sha256=4824850d…；恢复演练复制 19 个文件到临时根后 `history` 可读、`finish` 核对 completed 产物哈希（含 review.md）通过、`source-read` 可读原文。

与计划的差异及原因：无产品范围差异。工程默认值并记录：`compare` 对任一侧 null 的分值输出 null（不适用）；备份只复制已归档来源（`data/stocks/`）与 completed run 产物，元数据型教学来源不计入 missing；CLI 新增捕获 `storage.StorageError`（锁超时 exit 1）。复核提出的两处 as_of 可用性缺口均在 `_is_core_eligible` 收口：provider 不再只信任预计算的 `observed_after_as_of`，而是直接比较 `metadata.observed_at` 与 `draft.as_of`；`publication_precision=date` 按当地日终保守判断。

已知限制：复评变化解释文字来自草稿 `review_explanations`（本阶段无模型生成，留空时 review.md 明确标注）；备份为本地一致快照，无云同步/增量/加密；未实现估值、附记正文、MCP/LangGraph/DeepSeek。

## 阶段 5 记录

阶段：5，OpenCode + CLI 端到端研究（含阶段 4 的 as_of 可用性小修复）。

完成日期：2026-09-20。

新增/修改文件：`skills/company-research/SKILL.md`、`opencode.json`、`src/wonder_stockresearch/research.py`、`tests/test_evidence_as_of.py`、`tests/test_sources.py`、`docs/05-opencode-and-tools.md`、`docs/research-spec.md`、`docs/technical-spec.md`、`README.md`、`PLAN.md` 和本文件。

实际运行命令与结果：

- `uv run pytest -q`：47 passed（阶段 1–4 的 39 个测试保持通过，新增 8 个）。
- `uv run ruff check .`：All checks passed!。
- `uv run wonder-research --help`：退出码 0，列出含 `collect` 的全部命令。
- 真实端到端（项目根，HK.00728）：`start` → `collect --kind filings`（selected=4）→ `collect --kind market` → `sources`（7 条）→ 多段 `source-read` → `save-draft`（issues=[]，source_count=7）→ `validate`（exit 0）→ `finish`（completed，`quality_status=evidence_incomplete`，`score_total=null`），生成 `data/stocks/HK.00728/runs/20260920T162000Z-2d441bba-…/{company.md,research.json}`。
- 跨会话重建：新进程仅凭 run_id 执行 `status`（completed、24 问、7 来源、steps 三项 succeeded）、`sources`、`source-read` 均成功；重复 `save-draft` 被 `run_completed` 拒绝。
- 非法草稿：把 `source_id` 改为不存在的 UUID 后 `validate-packet` 退出码 2，报 `unknown_source` 与 `missing_authoritative_support`。
- 真实 as_of 修复验证：未知发布日期的 primary、晚于 as_of 的 primary、`observed_after_as_of=true` 的 provider 均不能支撑核心结论，报 `ineligible_as_of_support`；资料本身保留归档（`tests/test_evidence_as_of.py` 覆盖）。

与计划的差异及原因：无产品范围差异。工程补充并记录：save-draft 合并 run 已挂来源（`_effective_sources`），使宿主引用采集来源时 `sources` 可留空；`opencode.json` 用 `skills.paths: ["skills"]` 注册唯一正文。真实研究刻意保守：多数问题为 partial/missing，门闸仅 G1=pass，护城河仅 M5 非 null，总分未知。

已知限制：skill 需重启 OpenCode 才加载、无自动化测试；一次性研究仍有大量缺口；未实现 compare/backup、估值、附记；未接模型 API/MCP/LangGraph。

## 阶段 4 记录

阶段：4，真实资料与富途只读接入。

完成日期：2026-09-20。

新增/修改文件：`src/wonder_stockresearch/market.py`、`src/wonder_stockresearch/disclosures.py`、`src/wonder_stockresearch/research.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/cli.py`、`pyproject.toml`、`uv.lock`、`tests/test_run_steps.py`、`tests/test_collect.py`、`docs/04-data-connectors.md`、`README.md`、`docs/technical-spec.md`、`docs/provenance.md` 和本文件。

实际运行命令与结果：

- `uv run pytest -q`：39 passed（阶段 1–3 的 26 个测试保持通过，新增 13 个阶段 4 测试）。
- `uv run ruff check .`：All checks passed!。
- `uv add futu-api`：锁定 `futu-api==10.11.7108`。
- 真实 HKEX smoke：`collect --kind filings` 在 HK.00728 上 `candidate_count=18`、`selected_count=4`，下载归档 `2026 INTERIM REPORT` 与 `ANNUAL REPORT 2025/2024/2023`；`source-read --page 2` 返回 `locator="page 2"`、`total_pages=251`。
- 真实 Futu smoke：本机 OpenD 运行，`collect --kind market` 成功；快照返回中国电信 last_price 4.435；财务 4 类报表各 10 期年报数据，利润表/资产负债表/现金流量表为 CNY、国际会计准则，关键指标为 HKD。修正 `get_financials_statements` 需传 `statement_type=1..4` 与 `financial_type=ANNUAL`；`SysConfig.enable_console_log(False)` 避免 futu 日志污染 stdout。
- SEC smoke：`https://www.sec.gov/files/company_tickers.json` 在缺少带联系方式 User-Agent 时返回 403，SEC 通道标 blocked，需 `WONDER_SEC_USER_AGENT`。

与计划的差异及原因：无产品范围差异。工程调整并记录：HKEX 检索 `rowRange` 放宽到 1000 再过滤到 ≤20 条相关候选，否则前 100 行只覆盖约 1 份年报；新增 `market.py`/`disclosures.py` 而非旧账户模块；`run_steps` 经 `_MIGRATIONS` v1→v2 加入。真实 smoke 未覆盖美股端到端与 SEC 通道。

已知限制：富途财务仅 ANNUAL、每类型 ≤10 期，item 级单位 futu 未提供故不推断；公告按英文标题分类；SEC 需环境变量且本环境未验证；不做 OCR、模型写作、估值、附记正文或 OpenCode skill。

## 阶段 3 记录

阶段：3，来源存档与证据定位。

完成日期：2026-09-20。

新增/修改文件：`src/wonder_stockresearch/sources.py`、`src/wonder_stockresearch/research.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/models.py`、`src/wonder_stockresearch/cli.py`、`pyproject.toml`、`uv.lock`、`tests/test_sources.py`、`docs/03-sources-and-evidence.md`、`README.md` 和本文件。

实际运行命令与结果：

- `uv run pytest -q`：26 passed（阶段 1–2 的 15 个测试保持通过，新增 11 个来源测试）。
- `uv run ruff check .`：All checks passed!。
- `uv add requests 'pypdf>=4'`：安装 requests 2.34.2、pypdf 6.19.0 等。
- 本地文件端到端：source-add（file）→ sources → source-read，全部退出码 0，字节与文本归档到 `data/stocks/SYNTHETIC/sources/`。
- 真实 URL smoke：`source-add` 导入 `https://example.com/`（secondary），成功，extension=html、extraction_status=available。
- 私网拒绝：`http://127.0.0.1/x` → `blocked_host`，退出码 2。

与计划的差异及原因：无产品范围差异。工程补充：`sources` 表用 `source_json` 保存完整来源快照（阶段 2 已加），本阶段沿用；归档字节校验只对 `data/stocks/` 下的来源生效，避免阶段 2 的教学元数据来源被误判。URL 真实 smoke 使用了 `https://example.com/` 作为最小验证，未对真实公司披露做完整抓取。

已知限制：不执行网页脚本、不做 OCR；扫描件提示人工补文本；`source-read` 的 `--page` 依赖文本中的 `\f` 页边界；无公告自动发现、无富途/SEC/HKEX 接口；附记与估值未实现。

## 阶段 2 记录

阶段：2，SQLite、草稿与股票历史。

完成日期：2026-09-18。

新增/修改文件：`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/research.py`、`src/wonder_stockresearch/cli.py`、`src/wonder_stockresearch/models.py`、`src/wonder_stockresearch/reports.py`、`tests/test_storage.py`、`docs/02-storage-and-history.md`、`README.md`、`PLAN.md`、`docs/research-spec.md`、`docs/technical-spec.md` 和本文件。

实际运行命令与结果：

- `uv run pytest -q`：15 passed（阶段 1 的 7 个测试保持通过，新增 8 个阶段 2 测试）。
- `uv run ruff check .`：All checks passed!（初始 4 个 UP017 已修正）。
- 临时项目根端到端实跑：watch add → start → save-draft → status → validate → finish → history 全部退出码 0；`finish` 生成 `data/stocks/SYNTHETIC/runs/20260918T135947Z-<run_id>/{company.md,research.json}`，报告头为“正式归档”。

与计划的差异及原因：无产品范围差异。两处工程补充：`sources` 表增加 `source_json` 保存来源完整快照（技术规格只列了部分列），以及 `runs` 表按用户 2026-09-18 决定增加 `kind`/`parent_run_id` 预留附记与专题；均不改变产品语义。`finish` 在有 baseline 时只保存关联，不生成 `review.md`，比较留到阶段 6。

已知限制：来源只有元数据，未保存真实字节，`validate` 不检查原文文件；无网络/富途/公告发现；无 review.md/compare；附记正文、专题报告与估值未实现；SYNTHETIC 仍可在离线模式使用；写锁依赖 fcntl，不支持 Windows。

## 阶段 1 记录

阶段：1，离线公司研究闭环。

完成日期：2026-09-10。

新增/修改文件：`pyproject.toml`、`uv.lock`、`.gitignore`、`src/wonder_stockresearch/`、`tests/`、`docs/01-minimal-research.md`、`README.md` 和本文件。

实际运行命令与结果：

- 红绿循环：模型模块、校验服务、报告渲染和 CLI 均先以缺失模块或不满足断言的测试失败，再实现最小代码使对应测试通过。
- `UV_CACHE_DIR=/private/tmp/wonder_stockresearch_uv_cache uv run pytest -q`：7 passed。
- `UV_CACHE_DIR=/private/tmp/wonder_stockresearch_uv_cache uv run ruff check .`：All checks passed!
- `UV_CACHE_DIR=/private/tmp/wonder_stockresearch_uv_cache uv run wonder-research --help`：退出码 0；显示 `validate-packet` 和 `render-packet`。

与计划的差异及原因：无产品范围差异。测试 fixture 使用 Python 函数构造完整虚构 JSON，并在 CLI 测试中序列化到临时路径；不在项目 `data/` 留下模拟研究报告。

已知限制：Pydantic 与纯函数只校验数据结构、来源 ID、类别和已知发布时间边界，不验证原文存在或结论语义；无持久化、网络、真实资料、账户访问、交易、估值或模型 API。

## 最新决策

- 独立 wonder-stockresearch 项目，不在旧项目下建 simplified/。
- Python CLI 优先，先由 OpenCode 使用；MCP、Agent SDK、Pi 对比均不属于当前首版阶段任务。
- 公司问题与估值分开，估值具体设计推迟；不为凑齐 28 问自行实现 OE/DCF。
- 数据来源和元数据留档、SQLite 与按股票文件夹保留；不复制旧版事件溯源和发布状态机。
- 每阶段完成后暂停并讲解。
- 2026-09-18：报告分三层——标准公司研究（季报级，完成后不可改）、挂在标准研究下的附记（季度间小更新，估值内容预留于此）、单独成报的专题（本版不实现）。阶段 2 只落标准研究并预留 kind/parent_run_id/估值位置，不做附记正文与估值计算。
- 2026-09-20：架构演进决策——领域核心继续使用函数、CLI、SQLite 与文件；阶段 4 增加轻量 `run_steps` 执行明细；阶段 5 默认由 OpenCode skill 人工监督；先做直接 writer，再根据真实端到端运行证据决定是否在可选阶段引入 LangGraph。Temporal、Prefect、多 Agent 和 MCP 不提前进入首版。
- 2026-09-20：阶段 4 真实接入边界——富途只用 `OpenQuoteContext` 的行情/财务接口，不导入账户/交易接口；OpenD 主机端口由环境变量配置。SEC 需 `WONDER_SEC_USER_AGENT`，未配置时该通道 blocked，不影响 HKEX。provider 数据保留 `period/currency/accounting_standards/field/endpoint` 与 `observed_at`，futu 未给单位时不推断。
- 2026-09-20：核心证据 as_of 可用性——未知发布日期的 primary 只作 context；`published_at` 晚于 `draft.as_of` 或 provider `metadata.observed_after_as_of=true` 都不能支撑核心结论，覆盖 answered 问题、pass/fail 门闸与非 null 评分；资料仍归档不删除。另：save-draft 合并 run 已挂来源，供宿主引用采集来源。
- 2026-09-20：阶段 5 宿主接入——project skill 唯一正文在 `skills/company-research/SKILL.md`，由 `opencode.json` 的 `skills.paths` 注册；OpenCode 只作操作员，不接 MCP/模型 SDK，研究事实以 SQLite 与 data/ 为准。
- 2026-09-21：发布范围调整——阶段 7 的 Python 直接 DeepSeek writer 成为本版本必做交付；它只基于已归档材料生成并校验草稿，必须由用户显式 `finish`，不得自动检索或自动定稿。LangGraph 顺延为阶段 8 之后、满足门槛才评估的可选编排。
- 2026-09-21：阶段 6 复评/备份语义——`compare` 对任一侧 null 的分值输出 null（差值“不适用”），schema/口径不同则 `comparable=false` 不算分差；本次新增来源与沿用基准来源按 run_sources 集合差分列，旧来源保留原始时间；`finish` 有 baseline 时写 `review.md` 并纳入哈希；`backup` 在项目写锁内用 SQLite backup API + 文件拷贝，输出 manifest 与 RESTORE 说明，不覆盖已有备份。
- 2026-09-21：阶段 7 DeepSeek writer 边界——单供应商、单次调用、不自动 `finish`；`ticker`/`as_of`/`schema_version` 由 Python 覆写，模型只能用已归档 `source_id`/locator；凭据仅从 `DEEPSEEK_API_KEY` 读取，不落库/日志/错误信息；`run_steps` 经 v2→v3 迁移新增 `metadata_json`，只记模板版本、usage、finish_reason，不记提示词或隐藏推理。
- 2026-09-22：阶段 7.2 writer schema 修复——Prompt 明确 `Answer.gap` 的 `null`/完整对象契约；仅 schema 校验失败时最多进行一次结构修复，修复失败保持 `invalid_schema`，不保存未经校验的草稿；metadata 只记录受控的调用与校验摘要。
- 2026-09-21：按用户明确要求支持项目根 `.env`——`envfile.load_env_file` 解析 KEY=VALUE、不覆盖已有环境变量、不打印；`cli.main` 与 `writer.write_draft` 都会加载。`.env` 已加入 `.gitignore`、不提交，仓库只保留无密钥的 `.env.example`；覆盖 AGENTS.md 原“不写入项目”的一般表述（本地凭据文件明确不进入版本库）。
- 2026-09-21：writer 采样参数——DeepSeek `deepseek-flash` 默认 thinking 开启且 thinking 模式下 temperature 不生效、推理 token 计入输出预算；结构化写作默认 `thinking=disabled`、`temperature=0.2`，可经 `--thinking`/`--temperature` 或 `WONDER_DEEPSEEK_THINKING`/`WONDER_DEEPSEEK_TEMPERATURE` 覆盖，参数写入 `run_steps.metadata_json`。
- 2026-09-21：富途基本面接入与 as_of 方案 4——新增 `futu_fundamentals.py` 与 `collect --kind fundamentals`（`all` 包含），只调 `OpenQuoteContext` 只读接口；provider 带 `available_from`（报告期末/披露日）且不晚于 as_of 的已报告数据可支撑核心结论，实时快照（行情/估值/评级）仅 context。改 research-spec §6 与 `_is_core_eligible`。富途全量能力清单见 `docs/08-futu-data-capabilities.md`。

## 阶段记录模板

阶段：
完成日期：
新增/修改文件：
实际运行命令与结果：
与计划的差异及原因：
已知限制：
下一步：

## 交接核验

核验时间：2026-09-10T14:46:50.415910+00:00。

- 对全部 8 份 Markdown 执行 Python 静态核验，退出码 0。
- 相对 Markdown 链接均可解析；代码围栏成对。
- Q01–Q24、六项公司门闸、M1–M6 共 36 个规格行各出现一次。
- 无待填 TBD/TODO、无旧项目绝对路径依赖、无提前生成的实现文件。
- 人工复核了公司/估值分离、CLI 优先、每阶段暂停、来源时间边界以及完成重试语义。明确补充了晚取得 provider 数据不能冒充 as_of 当时数据的处理。
- 这些检查只验证交接文件的结构与一致性，不代表代码、接口或研究效果已通过测试。

## 补充交接：阶段 7 的 DeepSeek API 写作

用户确认本版本在阶段 6 后必须能够直接接 DeepSeek API 写报告。已同步 README.md、PLAN.md、技术规格第 8 节和本文件：阶段 7 复用 ResearchDraft、证据、校验和渲染服务，不依赖旧会话，也不提前开发 Agent 循环。当前没有安装 SDK、调用模型或读取凭据。
