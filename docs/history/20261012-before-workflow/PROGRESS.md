> 历史快照：截至2026-10-12整理前，仅供追溯；现行规则请读仓库根入口。正文保留，相对Markdown链接已重定位。

# 项目进度

## 2026-10-11：澄清 Skill 与五题通用修复（离线完成，真实质量待复测）

用户明确要求创建类似 grillme 的小 Skill，并结合前述计划一起处理五题。本轮按最新指令合并 G1–G3 的正确性修复及 G4 离线回归；没有按 ticker、公司、题号或完整问题写专用分支。没有将本轮视为重新授权真实模型额度。

实际实现：新增 `skills/question-clarification/{SKILL.md,skill.json,evals/evals.json}` 和 `interactive/question_requirements.py`、`interactive/numeric_answer.py`。ask/chat 共用入口冻结分析时钟、方法哈希和要求版本；有限相对年/明确年份季度解析与同一 Agent 方法指导结合，必要才问，回复仍在原 Turn 新 Attempt。要求事件保存已处理补充，防止旧“是”确认新候选；明确年份不多问，用户改变季度目标会更新完整季度清单。自然年度与未知/非12月期末财年不能直接替代。

数值原始输入由成功 SQL 返回的 fact_id 反查真实快照，保存在 verified_facts；模型提交 numeric_blocks 的产物/事实 ID 和通用运算名称，Python 校验公司、Turn、哈希、实际回执、期间、原值/同比/环比、目标覆盖和所需运算。Decimal 计算并渲染 values/mean/range/population_stddev/changes，排序保持年份和值身份；未知单位不补百分号/百分点，未知单位或倍率不随意跨来源合并。缺年季先确认，正文/claims 不另抄数字/趋势。AST 错误说明具体调用名，无工具收尾不误称没有计算器，提交格式/引用/数值错误预算内最多纠正一次，retry不重置。

`agent_company` 增加通用资产/负债/现金候选别名和原值优先排序；目录指向 SQL 取值。SQL/公司查询返回已解析期间覆盖，执行层记住本范围已知季度缺口，阻止等价空查但保留其他授权来源/补取路径。chat 存储 waiting_user/respond_interactive，补充不经过 Router；后续新 Turn 执行前建立关联，失败仍可恢复正确问题。现有工作清单纳入要求/覆盖/数值产物摘要。legacy 单轮输入遵循 Turn 已冻结上限，旧 Turn 缺字段保留旧规则，累计预算不变。

主要修改：`agent.py`、`chat.py`、`models.py`、`interactive/agent_company.py`、`agent_execution.py`、`agent_session.py`、`agent_policy.py`、`agent_results.py`、`agent_memory.py`、`agent_working_record.py`、`legacy_agent.py`、`query_workspace.py`；Pi 共用提交路径包含 `pi_attempt.py`。`pyproject.toml` 打包同源内置 Skill；同步 README、PLAN、技术规格、09、provenance 及 [中文教学40](../../40-question-clarification-and-numeric-answers.md)。已有测试按新确认/数值协议更新，没有删除预算、授权、引用和历史冻结断言。

实际命令与结果：

- 本轮开始 `./.venv/bin/pytest -q`：416 passed。首次改动后存在旧协议预期/上下文大小等失败，逐项修正后执行以下最终检查，不预填通过。
- `./.venv/bin/pytest -q tests/test_question_clarification_and_numeric_answer.py --tb=short`：**41 passed in 4.22s**。虚构归档/脚本模型覆盖澄清、确认、语义补充、chat恢复、错配、单位、统计、字段定位到真实数值、空查、越界、方法哈希和跨重试纠正限额。
- `./.venv/bin/pytest -q --tb=short`：**457 passed in 13.43s**。
- `WONDER_AGENT_RUNTIME=pi ./.venv/bin/pytest -q --tb=short`：**457 passed in 18.83s**。包含双runtime用例，未调用真实API。
- `./.venv/bin/ruff check src tests scripts`：All checks passed；`git diff --check` 通过。
- `uv build --offline --wheel --out-dir data/build/question-clarification`：成功。首次独立临时缓存没有 hatchling，默认缓存被沙箱阻止；获准后离线使用已有缓存构建，无新增依赖或网络请求。最终 wheel 比对11个包模块和3份Skill文件与源码字节一致；临时解压、`python -I` 独立加载包内Skill/Agent/chat成功，不含node_modules。wheel SHA256为`bcd4b3e64a0e397650498b29eb4b9f60fac8d68c2244ba8b7437d87390f878d7`。未发布或安装，TS无改动未重跑构建。

限制与下一步：离线测试证明契约和恢复路径，不证明真实模型稳定理解问题、选择工具或解释经营原因。三个Skill评测提示已保存，真实Skill A/B和真实五题均未运行；历史Pi 40/40额度未重置。有限解析不覆盖所有财历/滚动窗口；未知单位/披露状态不伪造映射。D1展开视图、D2全面SQL下推/快照复用/性能测量及其他派生公式契约仍为后续。无新增主库迁移、正式报告改写、交易/账户访问、Git提交或默认runtime/thinking切换。下一步优先同来源/预算五题质量复测并处理必要澄清，本轮完成后暂停。

## 2026-10-11：AI 产品经理面试 PPT 与配套材料（完成）

按用户确认的计划交付本地面试材料，目录为 `data/interview/agent-platform-pm/`（继续忽略，不提交业务资料）。11 页主讲约 10 分钟、7 页备份，面向 Agent／平台产品经理，采用 deepPPT 蓝白证据风。明确个人原型、质量验证中，用户负责需求/边界/优先级/验收/复盘，代码主要由 AI 辅助实现。按后续要求，第4页加入整体架构、第5页介绍4个基线Skill与制作期间新增的澄清Skill（开发中未验收），第17/18页展开文件级架构和Skill依赖/调用；架构沿用已保存导览的分层，按当前Pi、公司数据层和主Memo重绘，避免照搬旧导览的归档只读、单库描述。

实际产物：可编辑 `agent-platform-pm.pptx`、`agent-platform-pm.pdf`；`interview-guide.md` 包含每页讲稿、90 秒介绍、15 道问答和Skill速查；`evidence-index.md` 提供48项来源/代码哈希、55个关键Python入口行号及Pi钩子，含5份SKILL.md和4份manifest；source/snapshot保留本材料使用的文件副本；`source/` 保存材料整理、生成、渲染与验收源码，`preview/` 保存总览与18张单页图，`qa/` 保存检查记录。

内容直接核对代码与既有归档：历史 Pi 40 次执行的 34 completed/6 failed，与 30 案例最后一次审阅的 4 pass/5 partial/21 fail 分开展示；最新五题标为 legacy、2026-10-11，不作为 Pi 同条件对照。年份配反实际为销售毛利率 Q20，ROE R01 单独说明下降误写回升。恢复契约与G1–G4尚未完成质量验收明确区分；制作期间检测到question-clarification及question_requirements新文件，标为开发中未验收，没有虚构用户规模、准确率、节省时间或商业效果。

实际验收：PptxGenJS 4.0.1 生成后运行 normalize_pptx、check_pptx、extract_pptx_text、qa_layout 与 markitdown；结构与版式检查通过。LibreOffice + pdftoppm 渲染全部18页；首轮中文缺字经产物目录内字体配置修复，使用系统Arial Unicode MS替代渲染，PPTX仍声明Microsoft YaHei。独立视觉复核原16页并复看9/10/14页；扩充后查看18页总览及4/5/17/18单页，按建议删悬空虚线、明确模型由Router/Writer/Agent共用并上移底部数据框。交付检查确认18页/18份讲者备注、2张原生图表及嵌入工作簿、10张原生表格、每页60段模板规则、无画布越界、PDF/PNG均18页；Decimal复算Q11/Q20统计量，离线检查4个基线Skill、3份能力manifest、共用依赖及工作台8个工具声明；新增澄清method单列，不验收业务接入或行为。

限制：本机没有 PowerPoint，原生 PowerPoint/其他设备字体兼容未验证；建议演示设备先检查或使用已渲染 PDF。未安装新依赖，未重跑业务 pytest，未新增项目模型/OpenD请求，未修改业务代码、主库或正式报告，未重置40次历史额度。只完成19D.2中的本地面试材料部分，上手/开源可发现性材料仍待实施；下一步可使用讲稿演练，本轮不自动开始业务修复或新评测。

## 2026-10-11：问题澄清小 Skill（设计纳入 G2，未实施）

用户建议借鉴 grillme，让 Agent 每次先确定问题。已核对当前 chat 的执行 Skill 加载与直接 ask 的默认空上下文；只新增一个由 Router 选择的 Skill 不能覆盖所有开放问答。39计划3.3.3及PLAN补充：同一个 Agent 使用简短方法 Skill，每题检查、必要才问；区分用户意图歧义与可只读查证的资料，确认结果在 Turn 工作记录中恢复。拟接入 ask/chat 共用执行层，覆盖 legacy/Pi、追问/恢复和版本审计；Python 处理已知必要条件与等待状态，不把模型“理解了”当作确认。验收同时检查漏问与多问，保留明确问题直接执行。本轮只更新三份计划文档，未创建 Skill、修改业务代码、运行pytest或新增真实模型/OpenD调用。

## 2026-10-11：相对时间的实现分工与RAG/复核备选（设计补充）

按用户追问，在39计划3.3.2和PLAN明确 prompt/现有模型负责自然语言候选解释，Python负责基准日期、覆盖、结构化期间选择、确认门槛及最终核对。任务要求与可得数据分开，未确认含义不凭模型置信度执行；不按“近3年”硬编码具体年份，也不以RAG或第二个模型替代用户意图澄清。查阅RAG原论文及Anthropic evaluator-optimizer一手说明，引用写入计划；复核模型仅是未来需评测的备选，本轮没有新增多Agent、向量库、依赖或真实问答。只更新设计说明，未改业务代码、未运行pytest。

## 2026-10-11：相对时间解释与先追问规则（计划补充，未实施）

用户指出“近三年”可能被解释为旧三个年度且没有先追问。核对 agent_session.initial_context/agent_policy 与共用 accept_answer：目前已有 ask_user→waiting_user/ask-respond 机制，但缺少专门的基准日期与期间解释对象，也没有必须澄清的程序门槛。不能仅凭一次错误归因为模型训练问题。

已补充39计划3.3.1及PLAN：程序提供日期/时区/财年/实际披露覆盖，结构化区分完整财年、自然年和滚动区间；目标期间与数据可得期间分开。明确年份或已确认口径可直接执行，存在歧义/资料滞后/披露未知则先列年份候选并等待用户，禁止只在最终缺口中补一句需确认。固定时钟测试覆盖跨年、不同财年、缺最新年、已有确认和恢复预算。仅更新计划，未改业务实现、未运行pytest或模型/OpenD。

## 2026-10-11：最新五题真实结果复盘与通用计划（文档完成，修复未实施）

用户完成 data/evals/diagnostic-5-ask-20261011-175339-528621，要求解释问题并采用 general 改法。已读取五份 JSON，以 sqlite3 mode=ro 查询 wonder.db/company.db 的当前 Turn、事件及来源；读取 SQL/成功函数产物和两份归档，哈希与来源登记一致。本轮不初始化/迁移业务库，不执行 Agent，不新增模型/OpenD 请求。

实际发现：4 completed/1 failed，但不能作为4题通过。Q20 将五个数按值排序后丢失期间，再错误配回年份；原始数值统计的均值 59.560814、极差 3.919337、总体标准差约 1.4073838336 复算一致，未知单位仍被写成百分点。R01 有 2025 数据但主回答取2021–2024，且把2024→2025的原值差 -0.190011 写成连续回升；R01/Q11 各三次语法拒绝，executed=false。Q11 最近三值可算均值，却声称系统没有计算能力；空工具收尾 prompt 确实包含能力误述。Q08 九次目录查询未取值，自写 locator 被回执检查正确拒绝；源数据存在资产合计/负债合计等候选，需核实口径后选择。Q23 statements 索引仅年度，季度缺口合理，但三次 schema/三次空 SQL 缺少进展。五题记录用量合计152,987 tokens，未核对账单。

新增 [docs/39-five-question-review-and-general-plan.md](../../39-five-question-review-and-general-plan.md)，更新 PLAN 最新优先级及36计划后续索引。用户的通用性要求落实为 G1 能力/错误纠正 → G2 身份/单位/期间/趋势的结构化产物与程序渲染 → G3 字段别名候选、真实可见分页、覆盖诊断及无进展循环 → G4 同条件、多公司/未见指标验收。保留D1但不优先于正确性修复；禁止按公司、题号或原文分支，不新增任意工具权限，不把目录当可引用事实。

实际验证：Decimal 复算上述统计量、三值均值及相邻原值差；通用 sum/len/float 平均函数通过现有 validate_function 语法检查，未运行真实分析；build_system_prompt 空工具探针复现误述。4份相关 Markdown 的代码围栏和39个本地链接检查通过，git diff --check 通过。本轮只文档/复盘，未重跑 pytest，既有416项通过不能证明本批质量通过。legacy 本批没有 agent_messages，拒绝代码未归档，未读 Langfuse 正文，故被拒调用的具体名称仍未知；不擅自归因为 Lambda。下一步按更新计划实施一个教学阶段，本轮未实施业务改动。

## 2026-10-11：用户五题初诊与下一批输入准备（未执行新增真实问题）

已读取用户生成的 data/evals/diagnostic-5-20261011-173913 五份 JSON，提取两份原报告及三项非报告状态为 reports.md，原 JSON 保持不变。ROE 等待选股，Q08 引用回执拒绝，Q23 输出字段不合协议；Q11/Q20 完成但单位表述存在未经确认的百分比/百分点，完整质量与工具 trace 尚未核验。前次 chat 批命令未处理候选选择，已说明。

按用户要求准备 data/evals/diagnostic-5-prepared-20261011 的五份 Pydantic 合法输入、cases.json 与 run.py。脚本只逐题调用现有 ask CLI、固定 ticker/legacy/company_latest、禁止资料补取、每题 60,000 tokens/10 轮；不自动重试，终端显示正文/错误并生成逐题 JSON 与 reports.md。--check-only 只检查输入；通过该检查及真实 CLI ask --help 检查，未发起模型或 OpenD 调用。单位映射与结构化渲染、引用回执和协议失败的后续修改要求已写 PLAN，业务实现尚未改动。新真实五题由用户运行命令启动，与 chat Skill 路由入口不同，结果不能直接当作严格 A/B。

## 2026-10-11：每家公司一个主备忘录与 chat save（离线完成）

用户明确要求“每家公司一个主备忘录”，每次临时分析后输入 save 即可。已实现精确命令保存，不调用模型重写；代码与 [中文教学](../../38-company-memo-save.md) 一并交付，保留前轮工作区改动，未提交 Git。

实际文件与变化：

- `storage.py` 增加 schema v14 的 `company_notebooks` 和 `company_memo_links`：每 ticker 一个主 ID，旧 `company_memos` 保留为不可变条目，按追加顺序读取历史版本；迁移保留旧条目内容/ID/标题版本。主 Memo 不因问题标题改变而分裂。
- 新增 `interactive/company_memo.py` 管唯一主备忘录、条目顺序、历史版本、Markdown 渲染与 data 内原子导出；文件固定为 `data/stocks/<ticker>/memo.md`。数据库为权威内容，文件导出失败可重试修复。
- `agent.py` 增加 `memo_save_report()`，校验当前报告归属、来源及 company_latest 的本 Turn 回执，收集顶层与 claims 内引用，保存原回答/结论/反方/缺口/后续检查。条目、关联、长期记忆在同一事务；同一报告重复快捷保存不追加。当前失败或等待不能回退保存旧回答，没有来源的诚实缺口可保存并明确标注。
- `chat.py` 在 Router 前处理独立 `save`、`/save`、`保存`，使用当前 chat 已关联的 Agent 会话，成功后仍可追问。`cli.py` 提示快捷保存，支持通过 chat ID 恢复保存；memo-save 不传 JSON 可直接保存报告，保留手工 JSON 入口；memo-read 支持 ticker/主 ID/旧条目 ID 和主 Memo 历史版本。
- `interactive/agent_tools.py` 读取每公司唯一主 Memo，仍验证公司隔离；没有新增 Agent 写工具。AGENTS、README、09、技术规格、出处、PLAN 与 interactive-research-agent Skill 同步用户显式 save 授权及新语义。

实际验证：

- 新增 `tests/test_company_notebook.py` **12 项**离线用例，覆盖两 runtime、幂等保存/连续追问、重启恢复、无会话/待确认/失败拒绝、跨 Turn/公司隔离、claims-only 引用、来源变更拒绝、历史版本、v13 迁移、事务故障回滚、导出失败重试及实际终端循环。资料虚构，未访问模型/OpenD。
- 最终 `./.venv/bin/python -m pytest -q` → **416 passed（9.85 秒）**；`WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q` → **416 passed（15.33 秒）**；`./.venv/bin/ruff check src tests scripts` 通过。旧 Memo 列表测试改为主 Memo 单条 + 历史条目保留断言；旧迁移夹具排除 v14 新表，原预算/报告断言保留。
- `uv build --wheel --out-dir /private/tmp/wonder-company-memo-dist` 成功；核对 6 个关键模块与 wheel 哈希相同，临时解压后独立运行 CLI/memo-save/memo-read 帮助及新模块导入成功，不含 node_modules。打包只是本地安装完整性验收，未发布安装包；本轮没有 TS 改动。
- 13 份已改 Markdown 的代码围栏、165 个本地链接检查通过；`git diff --check` 通过。

限制与下一步：schema v14 会在用户下次运行程序时自动迁移；本轮未运行主库迁移/保存，未替用户保存上次真实分析。旧 Memo 只迁移索引，后续 save 才导出文件。当前按问题追加，不做主题合并或模型总结；以 ticker 为公司键，不自动合并跨上市代码；长 Memo 的模型读取仍有投影上限。默认 legacy/Pi 预览与 thinking 设置不变，真实模型/OpenD 新增执行 **0**，原 40/40 额度不变，不改正式报告/评分。此教学阶段完成后暂停，D1 公司视图、D2 SQL/快照及 D3 指标单位阶段未自动启动。

## 2026-10-11：字段检索、工作清单与计算恢复（离线完成）

用户明确要求将“查过的东西没有清楚工作清单”“找数据绕路”加入本轮并开始执行。已将 D2 的定位指导、紧凑目录和清单前置，与 P0 一起落地；保留原工作区规划改动，不提交 Git。对应 [实施计划](../../36-calculation-tool-next-plan.md) 与 [中文教学](../../37-agent-query-and-recovery.md)。

实际变更：

- `interactive/agent_company.py` 压缩长字段目录，显示覆盖数、最近/最早期间及省略；`agent_policy.py`、`tool_registry.py` 和 analysis-workspace Skill 统一“未知位置先跨授权数据集检索、已知字段可直接查”的策略。沿用现有同义词搜索，不为 ROE 硬编码路径。
- 新增 `interactive/agent_working_record.py`，从当前 Turn 成功/错误/blocked 事件构建已完成、可用输入、计算、当前错误和待办；成功事件保存紧凑结构化观察。legacy 的 context/memory 和 PiAttempt 均注入同一清单，压缩先缩样本/旧交互；来源和 analysis_id 不因语法失败消失。公开 `ask-status` 展示清单，成功回答后 pending 清空。
- `query_workspace.py` 对 sorted key 的单参数 Lambda 单独检查位置、作用域和表达式；支持字段、数值转换、元组排序。保留 12 函数，实际执行环境/工具说明同源；具体节点/位置进入 `ToolFailure` 和 Pi 可选诊断字段。实际运行前占额度，失败运行仍计入每 Turn 三次；语法拒绝不占执行额度。
- 共用 `agent_execution.py` 区分工具阻止与问答停止：首次重复失败给一次纠正反馈；累计三次可恢复错误或重复纠正机会用尽时预算内无工具收尾。最后一个决策也用于收尾；重启/retry 恢复原计数，无关成功不清零。Pi 原生 schema 拒绝也计入，授权/来源/协议错误仍终止。失败 span 和 `tool.blocked` guardrail 分开记录，blocked 明示未执行。
- `agent.py` 在执行前通知 chat 保存 Agent Session/Turn 关联；`chat.py` 失败后保留原 ID、错误和“重试”入口，恢复原 Turn/来源/预算。没有数据库迁移或 TS 包装改动。

2026-10-11 实际检查：

- 新增 `tests/test_agent_recovery_working_record.py` 的 **36 项**用例；两种真实循环用脚本模型验证“跨数据集定位→SQL→不支持写法→同参阻止→修改排序→有引用回答”，另覆盖目录分页/完整事实、允许/拒绝 Lambda、12 函数、失败执行额度、长交互与压缩、Turn 隔离、恢复首轮保留输入/错误、重启、累计失败、有限预算收尾、chat 失败前关联/原 Turn 重试及 Pi 原生参数错误。资料均为虚构，网络未调用。
- 最终 `./.venv/bin/python -m pytest -q` → **404 passed（8.29 秒）**；`WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q` → **404 passed（12.66 秒）**；`./.venv/bin/ruff check src tests scripts` 通过。首次全量发现目录新增元数据挤掉既有 facts 导航，已让短 facts 导航保持原形式；旧断言未弱化。最终复核补上 legacy 恢复首轮清单注入及 chat 重试原话保留，重新全量通过。
- `uv build --wheel --out-dir /private/tmp/wonder-query-recovery-dist` 成功（首次因沙箱无权读 uv 缓存失败，获准后重跑成功）。核对 8 个变更模块与最终 wheel 的哈希相同；临时解压后独立运行 CLI 帮助、导入新模块并校验排序 Lambda 成功，包内无 node_modules。本轮没有修改 TS，因此未重跑 TS 构建。
- README、PLAN、技术规格、09、27、36、37、出处及本进度同步实际状态；10 份 Markdown 的代码围栏和 150 个本地链接检查通过，`git diff --check` 通过。额外全仓 `ruff format --check src tests scripts` 提示 12 个未改动文件的既有格式差异，未扩大格式化范围；本轮变更 Python 文件格式检查通过，lint 仍通过。

已知限制与下一步：默认仍为 legacy、Pi 预览、thinking 关闭；新增真实模型/OpenD 执行 **0**，原 40/40 额度不变，原 30 题统计与历史 trace 不重写。没有运行主库业务/迁移或公司数据刷新，不改正式报告/评分/Memo。提示词可指导检索但不能保证真实模型选对字段；清单有大小上限、成功产物不证明自然语言结论正确。D1 公司展开视图、D2 SQL 下推/快照复用、D3 口径/单位映射与结构化数字回答及同快照真实复测仍待实施。代码、必要测试与教学已完成，按阶段约定暂停。

## 2026-10-11：ROE trace 复盘后的优化建议（计划记录，未实施）

用户随后要求把受限函数说明加入下一轮，并写出详细步骤。新增 [受限计算工具与有界错误恢复计划](../../36-calculation-tool-next-plan.md)，列出 12 个现有函数、简单排序 Lambda 的位置/作用域边界、各文件职责、一次额外纠正反馈与预算内收尾、产物/恢复/chat/观测规则及离线验收矩阵；同步 PLAN 的 P0 和技术规格待实施索引。基础 scratchpad 已存在，本轮规划增强成功输入和错误定位；原排序 Lambda 计划支持，验收相应改为成功路径，另造不支持写法验证纠正机制。仅记录下一轮要求，未修改业务代码。

本次细化的实际检查：PLAN、PROGRESS、技术规格和新增详细计划共 4 份文档的代码围栏、41 个本地链接及主要源码路径检查通过；`git diff --check` 通过。未运行 pytest 或真实接口；计划中的测试命令均明确为尚未运行。

用户要求把前述优化建议写入后续计划。已在 [PLAN](../../../PLAN.md) 登记 trace 基准、实施顺序与验收条件，并同步 [公司数据库计划](../../27-company-database-plan.md) 的 D 阶段细分及 A/B 实际状态。

- 优先修复受限计算出错、原样重试后无回答，以及失败 chat 与 Agent 会话关联；统一两种 runtime 的 prompt/Skill/tool 契约与有界恢复。
- 数据侧按公司展开视图 → 精确查询/紧凑字段目录/SQL 下推及快照复用 → 指标口径与单位映射推进，保留 source_batches 的公司归属、来源版本、旧 Turn 范围和证据定位。
- 将紧凑工作记录、结构化计算到回答、失败/阻止调用观测与同快照评测纳入对应阶段；真实模型复测仍须另有额度，不重置原 40/40。

实际检查：PLAN 与公司数据库计划的代码围栏、25 个本地链接检查通过；`git diff --check` 通过。本轮仅文档变更，未运行 pytest，未改代码或数据库，未新增模型/OpenD 调用、依赖或 Git 提交。下一教学阶段尚未启动，按约定暂停。

## 2026-10-10：混合架构 B——执行边界、目录分包与 Pi TypeScript 包装（离线完成）

用户要求按计划重构、需要讨论的变更先确认，测试可在架构完成后集中执行。先将执行边界阶段写入 PLAN；用户随后分别明确同意 Python 分包，以及仅将原 `pi_runtime/runner.mjs` 的 SDK 包装源码改为 TypeScript。本轮落实三项，保留此前工作区改动，未提交 Git。

实际文件与变化：

- `agent.py` 保留公开会话/Memo/授权入口；`interactive/agent_session.py` 提供生命周期与事务，`agent_execution.py` 提供共用预算、工具执行与答案接收，`legacy_agent.py` 是旧 JSON 适配器。Pi 不再反向调用入口模块的私有实现；`interactive/pi/pi_agent.py` 准备运行，`PiAttempt` 用显式状态和命名方法适配事件。
- 新 Turn 固定运行时，respond/retry 读旧 Turn 快照，follow-up 才选择当前配置；未知或损坏标记在开始 Attempt 前拒绝。两条运行时共用预算预留/结算、成功工具恢复、重复/错误处理、回执与最终事务。公共 usage 仅接受非负整数，排除 bool/负数/字符串等；未知用量保留预留，不从不完整字段推算免费用量。
- `interactive/tool_contracts.py` 定义一份 Pydantic 参数和小型 ToolContext；Registry 直接绑定 18 个普通 handler，取消闭包到大分支的二次分发。授权、参数、Turn 归属在业务读取前检查，handler 按需加载。`agent_results.py` 统一内部 ToolOutcome/ToolFailure 与有界投影，完整结果先用于回执/产物，再交给模型；外部报告 JSON 不改。
- `interactive/pi/pi_protocol.py` 定义 JSONL v1 的严格配置/事件/回复、握手和递增 ID，bridge 校验并清理进程。Node 使用 beforeToolCall/afterToolCall、等待型 subscribe、transformContext、streamFn 与 followUp；可恢复错误标记 isError，致命 Python 回调直接终止 Node。SDK turn_end/agent_end 不提交项目成功；没有引入 coding-agent/Harness 或通用 bash/write 等工具。
- `model_request.py` 共用 Python 配置/HTTP/可见响应/trace，Router、公司识别和旧问答不再借用 Writer 私有传输。供应商错误正文不进入业务日志；隐藏推理不当作可见回答。Writer 分组写作、调用上限和缓存继续原规则。
- 将 28 个内部模块移至 `fixed`、`interactive`、`interactive.pi`，直接更新 import/测试/脚本/文档链接，不为每个旧路径加 shim。`financial_calculations.py` 单独拥有原输入/结果模型、归档/Capex 提取与确定性计算；Writer 和两种问答工作区直接复用，无公式复制、评分变更或数据库反向依赖。公开 CLI/agent、存储、数据源与公司库入口留在根包，业务 data 路径不变。
- `pi_runtime/src/runner.ts` 使用 SDK 原生 Agent/事件/消息/工具/流类型，新增 strict tsconfig 与构建/生成一致性脚本；生成根目录 `runner.mjs` 供 Python 启动。项目内新增并锁定开发依赖 TypeScript **7.0.2**、Node 类型 **22.20.5**；Pi 运行依赖保持 **0.83.0**，用户运行已构建包不需要编译器。

集中验证与实际结果：

- 架构完成后默认 `./.venv/bin/python -m pytest -q` → **368 passed（6.82 秒）**；`WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q` → **368 passed（11.07 秒）**。相比评审基线新增85项离线用例，覆盖协议、原生钩子/失败停机、冻结运行时、两路径错误语义、非法用量与预留、按需读取、结果投影和公共传输。首轮旧大状态测试因夹具只改 row.run_id 而触发归属检查，已改成真实且归属一致的 Run/Turn；未弱化产品检查。
- 在 `src/wonder_stockresearch/pi_runtime` 执行 `npm run typecheck`、`npm run build`、`npm run check:generated`，均退出0。`./.venv/bin/ruff check src tests scripts`、`git diff --check` 通过；分包后 CLI 总帮助退出0。AST 核对 fixed 无 interactive 导入，两个工作区不再导入 fixed_quality。
- `uv build --wheel --out-dir /private/tmp/wonder-architecture-dist` 成功。核验12项关键包文件与源码哈希相同，包含新 Python 子包、共用计算、TS/JS、配置/构建脚本/锁文件，不含 node_modules/build。临时解压 wheel 后独立加载 CLI 帮助和 Pi runtime 路径成功，未误用开发源码路径。
- 新增中文教学 `docs/35-agent-execution-architecture.md`；同步 README、09/30/33/34、技术规格、出处与 PLAN。10份相关文档的本地链接与代码围栏检查通过；源码链接随文件迁移同步。
- 全部问答验证为虚构资料、脚本模型或阻断网络的 fetch 替换。本轮真实模型/OpenD调用 **0**，不运行主库迁移/业务会话/正式定稿，不新增40/40真实额度。安装仅为用户确认的 TS 编译开发依赖；源码参考目录不作为运行依赖。

限制与下一步：默认仍为 legacy，Pi 显式预览，thinking 关闭；没有复测原30题，不宣称语义质量改善。两种上下文历史选择仍各自保留，本阶段仅共用工具结果投影；相关性工作记录、计算到最终数字/期间/单位的结构化渲染、查询快照复用/下推仍待后续阶段。原局部导入依赖环没有因搬目录自动消失。此阶段完成后按教学约定暂停。

## 2026-10-09：Pi 事件与钩子复用评审（文档完成）

按用户追问补充 `docs/34-architecture-options.md` 第 15 节，逐项对应现有进度、trace、工具前后处理、上下文、预算、保存、修复和取消。核对锁定的 core 0.83.0 与指定 coding-agent 源码，确认当前已用等待型 `Agent.subscribe`，而 `AgentSession.subscribe` 不等待异步回调；扩展 `pi.on` 的工具事件内部映射到 core 前后钩子。同包 `AgentHarness.on` 可纳入方案 C 评估，不新增事件框架或自动更换会话管理。

实际验证：纯离线 Node SDK 探针六项行为检查通过，覆盖监听等待、工具前后顺序、结果修改、调用阻止及 Pi turn 与业务 Turn 的区别；真实模型请求为 0。只改评审文档与本记录，没有业务代码变更、依赖安装、主库写入或 Git 提交；未重新运行全量回归。方案仍待选择，默认运行时、40/40 真实额度和 30 题质量结论不变。

## 2026-10-09：工具边界、函数简化与 Pi 复用补充评审（文档完成）

按用户追问扩充 `docs/34-architecture-options.md` 第 11–14 节：核对 core 与 coding-agent 的默认工具差异，说明本项目未实例化 bash/write 等通用工具；解释 Pydantic → JSON Schema → Pi AgentTool → Python handler 的实际链路。读取指定目录中 0.83.0 的 `defineTool` 源码，确认它主要保留 TypeScript 参数推导，无需为此引入 coding-agent。指出当前 handler 闭包再回到大分支的冗余，列出直接函数绑定、执行状态整理及可复用 SDK 函数的取舍。方案仍待选择，未重构业务代码或改变授权、默认运行时、预算及研究范围。

实际验证：`./.venv/bin/python -m pytest -q tests/test_tool_registry.py tests/test_pi_runtime.py tests/test_stage19_query_workspace.py` → **22 passed（1.51 秒）**；离线读取实际工具目录，登记 18 项、直接 ask 兼容目录 17 项，不含 bash/write/edit/通用 read/finish/memo-save。保留既有工作区改动，无模型/OpenD 调用、主库迁移、依赖安装或 Git 提交；真实额度不增加。

## 2026-10-09：现有功能架构方案评审（文档完成，待选择）

用户要求参考指定 Pi 学习项目和 Codex，比较当前功能的设计改进。本次新增 `docs/34-architecture-options.md`：核对锁定的 Pi 0.83.0 实际 API、Python/Pi 调用、上下文投影、查询快照、计算到文字及 Router/Writer 耦合；提出局部整理、明确混合架构和进一步使用 AgentHarness 三种选项，推荐明确混合架构。列出各项改法、成本、复用边界和验收条件；仅提供设计选择，未修改业务代码、当前规格、PLAN、运行时默认或研究方法，保留既有工作区改动。

实际验证：`./.venv/bin/python -m pytest -q` → **283 passed（4.89 秒）**；`WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q` → **283 passed（9.33 秒）**。新文档 13 个本地链接均可解析、代码围栏成对，`git diff --check` 通过。只运行离线测试，无真实模型/OpenD 调用、依赖安装、主库迁移、正式定稿或 Git 提交。真实 30 题质量状态和 40/40 已耗尽额度不变。下一步由用户选择方案；本次不自动开始重构。

## 2026-10-09：当前架构中文总览（文档完成）

用户要求新增 Markdown 解释架构变更。本次新增 `docs/33-current-architecture.md`，按当前源码讲解固定研究与开放问答、Python/Node-Pi 分工、JSONL 事件、双库/隔离快照、Skill/Tool 授权、证据回执、Session/Turn/Attempt、预算、记忆和 trace；更新 README 架构图及阅读入口。明确 Pi 仍为显式预览，默认 Python loop 保留，company.db 是可重建事实投影，SQL 不在业务数据库执行。未改变产品或研究规则，保留既有工作区改动。

实际验证：`./.venv/bin/python -m pytest -q` → **283 passed（5.08 秒）**；`WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q` → **283 passed（9.36 秒）**；`./.venv/bin/ruff check src tests scripts` → All checks passed；CLI 总帮助与 company-data 帮助均退出 0。仅运行离线测试和文档核对，没有真实模型/OpenD 调用、依赖安装、主库迁移、业务数据修改、正式定稿或 Git 提交。

文档静态核对：新增章节、README、PROGRESS 共 3 份 Markdown 的 98 个本地链接均可解析、代码围栏成对；通过 AST 核对文中主要 Python 入口实际存在；`git diff --check` 通过。

限制与下一步：沿用原 Pi 真实验收未通过的状态；本次测试不证明真实研究质量改善，不增加 40/40 的已耗尽额度。后续真实复测与默认切换仍需按既有 PLAN 执行，本文完成后暂停。

## 2026-10-09：Pi 问答可靠性离线修复（完成，真实质量待复测）

用户确认按复盘计划修改，暂不用 thinking。先写入 PLAN，再实现本阶段；没有增加真实执行额度、切换默认或删除旧 loop，保留此前工作区改动，没有 Git 提交。

实际文件：新增 `pi_evidence.py`、`tests/test_pi_reliability.py`、`docs/32-pi-agent-reliability.md`；修改 models、agent、agent_tools、agent_company、tool_registry（v6）、agent_memory、pi_context、pi_agent、pi_runtime/runner.mjs、query_workspace 和原生 SDK 测试；同步 PLAN、README、技术规格、09/30/31 说明与本记录。

- 新问题默认 60,000累计tokens／10轮，允许显式收紧；Pi新增 input_token_limit（默认16,000）与 output_token_limit（默认4,096，最多8,192），固化新Turn。旧Turn的retry/respond不改预算，follow-up继承原Session；无新单轮字段的旧Pi Turn仍用原8,000/4,096。Writer/Router不变，thinking仍disabled。旧Python loop仍沿用原单轮策略。
- get_run_status在handler处直接摘要步骤/草稿。query_company_data增加精确field_keys、多periods、period_kind和offset分页及有限同义词检索。投影按实际可见行重算续页位置；大单项结果返回截断导航，避免无界进入模型上下文。
- 当前Turn可见调用重建有界工作记录，保留查询参数、事实样本、analysis_id、计算结果及续查状态；压缩按完整原生调用组处理，不修改原可见审计或扩展跨Turn证据权限。
- 紧凑ref_id由本Turn回执生成，Python展开后仍执行原公司/来源/回执校验；输出报告仍用source_id/locator。引用/结构修复给具体错误和合法引用，一次修复轮不再提供工具，Python也拒绝该轮工具调用，仍计入原预算。
- 计算产物增加实际输入字段/期间/单位覆盖摘要与函数哈希；SQL/函数错误返回具体纠正提示。没有为30题写死计算或用未知单位推断百分号，没有增加语义审查Agent。

实际验证：

- `WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q` → **283 passed（9.88秒）**；默认路径 `./.venv/bin/python -m pytest -q` → **283 passed（5.04秒）**。新增10个可靠性回归，原真实Pi SDK零网络fetch测试增加2个组合，覆盖工具关闭与thinking不变。
- `./.venv/bin/ruff check src tests scripts`通过；`git diff --check`通过；`uv build --wheel`成功。单轮输入上限在请求/预留前拒绝，预算继承、跨Turn引用拒绝、引用修复不重查、期间筛选、分页不跳行、计算覆盖和区间关系均有离线验证。
- 执行36的历史messages仅做离线投影：当前策略/工具定义下输入估算**7,768**，低于当时剩余**10,147**；记录为忽略的`data/audits/20261009-pi/offline-context-recheck.json`。与原20,865估算所用代码/提示不同，不能当作同条件模型性能对照或真实问答通过。
- 真实账本复核仍为**40/40**，本阶段新增真实模型/OpenD执行**0**。原30题的4通过/5部分/21失败不改写。
- 原冻结run文件哈希与旧审计快照一致。主库哈希与旧评测快照已不同，其中有2026-10-09 07:39–07:44 UTC的新会话记录；本轮未对主库执行Agent/迁移/恢复命令，也没有本轮开始前的字节快照，因此不使用旧快照宣称本轮主库字节不变，不回退已有会话。

已知限制：必要检索前提前final、错误文字解释、原始数据异常与真实资料缺口仍待真实验收；更高预算只是可用配置，并未证明60,000/10是最优值。工作记录是最近操作的有界摘要，不能保证无限任务记忆。中文教学含数据流、实际入口、配置、测试与练习。下一步需用户另行授权真实额度，先按同一资料快照对照工具修复与预算放宽，再决定完整30题及默认切换。按阶段约定暂停。

## 2026-10-09：Pi SDK 预览实现落地，质量验收未通过

用户完成 Q1–Q8 决策并要求写入计划后开始实施。已将混合架构、开放问答完整生命周期、Python 统一规范化记录、旧未完成问题兼容范围、仅新 Pi 的 30 题与最多 10 次额外真实执行、预算和严格切换标准写入 PLAN，并同步 AGENTS 的运行时边界。保留原有工作区改动，不提交 Git。

实际新增：`pi_runtime/package.json`、`package-lock.json`、`runner.mjs`、`pi_bridge.py`、`pi_agent.py`、`pi_context.py`、`tests/test_pi_runtime.py`、`scripts/eval_pi_agent.py`、`docs/30-pi-agent-runtime.md`、`docs/31-pi-agent-evaluation.md`。修改agent生命周期入口、原生提示词、storage schema 13的规范化消息表，以及对应测试/规格/README。项目内安装锁定的pi-ai/pi-agent-core 0.83.0，没有依赖参考项目、引入pi-coding-agent或提交Git。

- Pi接管原生provider、模型/工具循环和消息事件；Python继续处理授权、公司/来源范围、证据回执、计算、累计预算、Session/Turn/Attempt和报告事务。覆盖ask/followup/respond/retry/chat交互分支，status/interrupt/Memo继续复用Python。旧未完成Turn选择Pi时要求重新ask，拒绝发生在记录新Attempt/用户补充之前。
- Pi在内存运行，Python保存有序用户原话/可见助手/原生toolCall ID和参数/摘要及产物引用；恢复时只重建完整工具组。隐藏推理、诊断、认证头和完整请求不保存。模型请求前持久化预留，未知usage不产生免费重试；子进程中断会清理进程组。
- 迁移前基线261 passed（3.50秒）。最终 `WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q` → **271 passed（8.73秒）**；`./.venv/bin/ruff check src tests scripts`通过；`git diff --check`通过。新增测试覆盖真实pi-ai SSE的零网络fetch替换、原生调用ID/用量缺失、推理过滤、半组恢复、消息保存、旧Turn拒绝不改历史、冻结报告攻击、事实优先/长文/大状态投影、既有LLM trace用量。既有权限/等待/重试/预算/事务回归实际经Pi执行。
- `uv build --wheel`成功；核验wheel包括runtime源码/manifest/锁文件，不含node_modules。依赖安装使用npm锁文件，ignore-scripts；没有另装产品扩展。
- 只运行新Pi真实验证，共**40/40**：30题覆盖及10次冒烟/网络失败/定向复测。34次completed、6次failed；每题最后一次独立质量为**4通过、5部分、21失败**。不能以completed替代验收。首个请求未返回usage，账本保留30,000预留；全部记录781,602 tokens不等于精确账单。
- 全部真实请求写隔离副本，原主库和所有run文件逐题哈希检查不变；独立核验20处原始JSON定位及哈希，并用Decimal复算均值、增长率、标准差和区间关系。记录在忽略的 `data/audits/20261009-pi/`；最后一次逐题审阅为review.json，4个CLI trace及可见调用索引为trace-examples.md。
- 已修正强制JSON响应模式干扰原生调用、facts目录挤占、取得日期投影；40次结束后补了长介绍摘录、大状态计数摘要和原生LLM span/usage，有离线测试，**没有额外真实复测**。历史真实trace不虚构回填LLM span，原iteration/tool/decision树与messages/usage仍可读。

未解决阻塞：未完成必要检索/计算就final、预览省略或SQL失败被当成缺失、单位臆断、期间配错、心算错误、正确计算后的错误解释；限额内的定向复测仍未通过。Q11原始值均值已正确，但不足以代表整个Agent验收。没有运行旧Agent同条件真实对照，不能将失败率归因于Pi SDK。

按用户已确认规则，**不切换默认、不删除旧loop、不自动增加真实额度**。显式 `export WONDER_AGENT_RUNTIME=pi` 可检查预览。此阶段代码、离线验证、中文教学和真实审阅已记录，迁移验收仍未完成。下一步围绕有界事实投影、必要查询/计算完成条件、数字/期间/引用的一致性修复；真实复测需另行授权额度。用户随后询问失败根因与trace，已生成可直接阅读的四例复盘，没有额外模型调用。

## 2026-10-08：公司数据层 B——Agent 共享查询与来源固定（完成，按用户要求暂停）

用户要求继续 A 之后的 B，随后明确“这个阶段完成了先暂停”。当前阶段已收尾，不启动 C 的完整 30 题或 D 的架构/体积优化。

实际文件：新增 `agent_company.py`、`tests/test_company_agent.py`、`docs/29-company-agent-query.md`；修改 models/chat/agent、agent_data/agent_tools、company_data、query_workspace、tool_registry、agent_memory/agent_model、research.backup、analysis-workspace Skill 及对应规格/README/PLAN。保留原有工作区改动，没有提交 Git或安装扩展。

- 新 chat 使用显式 company_latest；直接 ask 的默认旧范围不变。Turn JSON 固定四类资料的 Source 与解析版本，重试不重选，追问选新范围且继承网络权限。新范围可复用他轮补取，旧 run 不扩权，无主库 schema 迁移。
- 公司字段/期间目录、少量事实和 SQL 快照 v2 已接入；profile、statements、operational_efficiency、revenue_breakdown 补取先归档/登记，再按所选来源索引。原始单位和口径不猜测，失败/partial/missing 分开。真实富途补取未执行，离线假供应商覆盖其路径。
- 最终报告/Memo 仍验证当前 Turn 回执、ticker 与归档哈希；补取替换查询版本后旧计算输入可追溯。目录不作为引用。公司范围初始上下文优先四类来源和可信长期记忆，不把 provisional 旧答案当原始数据。
- 真实调试发现固定说明占约 4,169 tokens，原 5,000 单次输入上限使三条输入也被压成一条。现按规则/最新结果动态分配，默认目标 5,000、最高 8,000；总预算仍为新 Turn 默认 30,000，真实 usage 结算，重试不刷新。可见 SQL 引用随可见行对齐；函数处理完整 SQL 行，不依赖聊天预览。
- backup 收录两库、来源、完成报告和已登记分析的 SQL 快照/查询/函数/结果，按数据库备份状态选择文件；遗漏/未完成状态与复制哈希均记录。虚构资料两库及计算产物恢复通过；完整主项目恢复未运行。

实际验证：

- `./.venv/bin/python -m pytest -q` → **261 passed（3.81 秒）**，比 A 新增 15 个用例；`./.venv/bin/ruff check src tests` → All checks passed；`git diff --check` 无错误。
- 中国移动真实归档的隔离副本：四类来源各一个，6,388 条财务事实、23 条基本字段；schema + SQL 首次约 0.36 秒。B 验证前后原项目主库 SHA-256 相同，未在原库运行真实 Agent；所有模型会话与计算留在隔离根。
- 同一原问题在六个开发版本上各跑一次真实模型；前五次 completed 但有误判缺失、反复查询或猜平均等质量失败，已保留诊断，不算通过率。最后一次走目录 → SQL → 模型编写函数 → final，4 次决策、14,252 tokens、5.781 秒。SQL 选中 2025/FY、2024/FY、2023/FY，三值 58.227439、58.436007、59.209519；Decimal 均值 58.6243216667，与函数结果和三处原文定位一致。
- **该真实答案仍不是完整合格：原归档 unit/scale 缺失，模型输出却用了百分号，并对期间/口径表述留下缺口。** 本阶段证明共享查询、真实计算、来源和预算链路，不证明每句语义正确。不能以 status=completed 或一次正确算术当作研究质量通过。
- 忽略的 `data/audits/20261008-company-agent/` 保存隔离副本、查询验证、六版模型结果与最终计算核对；无密钥/隐藏推理输出。文档记录实际结果和限制。已继续参考 futuapi Skill，以及用户指定 Dexter/Pi 核心设计；没有引入外部宿主或多 Agent。

下一阶段 C（尚未开始）：按同一快照执行已准备的 30 题，分别评分计算、期间/单位、引用支持、越权边界、预算与失败；优先修复已发现的单位臆断、未计算却宣称均值及重复查询。D 再根据测量处理 FTS、体积和职责简化。当前暂停，等待用户下一次指令。

## 2026-10-08：公司数据层 A——独立公司库、离线解析与回填（完成）

用户确认继续实施公司库计划。本阶段只交付 A，并提前让现有 backup 收录新库；没有切换 Agent 查询、调用模型/OpenD、更新外部公司数据或运行真实 30 题。公司库是归档投影，wonder.db 仍是来源登记、研究与会话的权威存储。

实际文件：新增 `src/wonder_stockresearch/company_parser.py`、`company_data.py`、`tests/test_company_data.py` 和 `docs/28-company-data-store.md`；修改 CLI、research.backup、README、PLAN、技术规格与 26/27 说明。没有提交 Git，保留已有工作区改动。

- schema v1 三表：source_batches、company_profile_facts、financial_facts。确定性解析四类 Futu envelope；原始字段/数值文本、Decimal、期间/会计元数据、分部/关键指标分组与 JSON 定位均保留。未知单位/披露时间不猜；data/yoy/qoq 分开，基础数据不按问答写公式。
- 来源身份、ticker 路径及哈希校验；主库连接只读、不触发迁移。成功批次按来源/哈希/解析器版本幂等跳过；失败可重试，整批事务防半份事实；新解析器版本保留旧投影。重建用临时库，任一 failed 不替换原库；危险符号链接拒绝。
- `company-data sync [TICKER]`、`rebuild`、`status [TICKER]`、`fields/read TICKER` 可运行。查询参数经 Pydantic/SQL 绑定、每表最多 200 行；人工结果明确 all_indexed_sources，没有冒充最新/历史时点或 Turn 证据授权。
- backup 在原写锁内额外生成可选 company.db 快照、manifest 哈希/版本与恢复说明，无公司库的旧项目兼容。虚构资料的两库备份恢复已测；尚未扩展被引用计算产物收录或进行完整主项目恢复。

实际验证：

- `./.venv/bin/python -m pytest -q` → 246 passed（3.26 秒，新增 20 个用例）；`./.venv/bin/ruff check src tests` → All checks passed；`git diff --check` 无错误。
- 隔离副本与本地真实归档分别完成导入：92 个历史来源，510 条基本字段、98,377 条数值事实；重复运行全部跳过。数值数量包含同比与历史来源版本，不是唯一经济指标数。
- 隔离数据的 98,377 个数值和 510 个基本字段全部按 locator 回到原始 JSON 核对值。七家公司最新经营效率/营收构成的 14 个来源在新库中均有 14–400 条数值；旧 Agent 临时解析器未改。
- 本地新公司库 47,652,864 字节（约 45.45 MiB）。一份旧中国电信营收构成归档包含九个历史获取失败，保留 partial；其余 91 批次 completed，无 failed。没有把接口失败当作零或未披露。
- 回填前后 wonder.db 文件哈希不变。实际 CLI sync 退出 0；`.venv/bin/wonder-research --root . company-data fields HK.00941 --dataset statements --limit 3` 与 `read HK.00941 --dataset profile --field 公司名称 --limit 1` 均退出 0。
- 忽略的审计目录 `data/audits/20261008-company-db/` 保存来源/主库隔离副本、`isolated-validation.json`、`value-validation.json`、`local-backfill.json`；该副本仅含本阶段四类原文及主库，不是完整项目备份。

教学说明含实际数据流、函数/DDL、命令、事务/Decimal 原因、测试、练习和限制。已使用本地 futuapi Skill 核对字段，未安装新 Skill 或运行接口脚本。

已知限制与下一阶段：字段仍为供应商原始语义，单位/披露时间缺失不能自动补全；重复获取事件会保留重复经济数值。新采集尚需手动 sync。下一阶段 B 才接入显式公司数据范围、字段目录/覆盖、profile 补取与补取后索引、跨 Turn 复用、本 Turn 证据回执，并核验计算产物备份；之后执行 30 题基线。按教学约定，本阶段完成后暂停。

## 2026-10-08：独立公司数据库设计建议（未实施）

用户进一步提出单独保存公司财务等数据。本次将下一阶段建议修订为所有公司共用 `data/company.db`，`wonder.db` 继续保存来源登记、研究与会话。新增 `docs/27-company-database-plan.md`，同步题集说明、PLAN、README 与技术规格，明确这是待实施设计。

- 公司库是原始归档的可重建、版本化投影，首版拟采用解析批次、基本字段、财务事实三类表，确定性解析覆盖四类现有资料；衍生指标仍由模型选择 SQL/受限 Python，不按问题写死。
- 先归档/登记 Source，再事务提交公司事实批次；来源身份与哈希由程序跨库校验，失败可幂等补索引。显式区分 company_latest、正式 run 与历史时点，旧 Turn/报告不自动换数据。
- 公司库实施需同步设计两库及引用文件的备份/恢复。只读统计显示主库约 81 MiB，FTS content/data 约 65 MiB；新公司库本身不解决现有全文索引体积。
- 实施次序：保留现状快照与题集 → A 公司库/解析 → B 共享查询/刷新/联合备份 → C 30 题复测 → D 按测量优化检索与代码组织。每阶段完成后暂停。此轮没有创建数据库、迁移资料或执行真实 30 题。

参考已核对的 SQLite 官方外键、ATTACH 与 Backup API 文档，链接见设计文档。本次仅文档调整；沿用本轮此前实际运行的 226 passed（2.92 秒）代码基线，不宣称公司库功能已通过测试。

## 2026-10-08：公司数据盘点与 30 道交互测试题准备

用户要求确认是否已有按公司保存的基本/财务数据，准备 30 道之后手工测试的问题，并提出下一步计划。本次只读盘点与准备题集，不执行这些真实问答，不实施数据库迁移或新的公司数据层。

- 只读主库与归档：当前 7 个 ticker 均有 profile、statements、经营效率和营收构成；最新四类共 28 个文件存在且哈希匹配。最新单个 statements 解析出 1,232–3,113 条数值、149–191 个不同科目标签；年度范围与逐公司结果见 `docs/26-company-data-and-agent-eval.md`。基本信息原文也有公司名称，但 stocks 的四条 name 仍为空。
- 明确现状：主库保存来源索引、研究与会话，报表主要为归档 JSON；没有持久 financial_facts 表，SQL 仍按次生成 QueryWorkspace 快照。Agent 默认 run 范围不等于公司全部最新资料，补取缓存限当前 Turn，profile 没有按需补取工具。七家公司经营效率/营收构成原文非空，但这 14 个来源通过当前数值解析器均得到 0 条事实；这是解析覆盖缺口，不是未采集。
- 新增 `docs/evals/interactive-agent-30.v1.json` 与中文说明 `docs/26-company-data-and-agent-eval.md`：六类各五题，覆盖基本资料、原始财务、基础计算、自定义计算、时间/缺口/离线数据与交互边界。Q29 依赖 Q11 的 session 做 follow-up；Q25 禁止资料补取；Q30 用 ask 测试正式研究写入拒绝。所有题带验收点，不预填数值答案或通过结果。
- 本机生成 `data/evals/manual-30/inputs/Q01.json` 至 `Q30.json`；30 份输入经当前 Pydantic 模型验证，题号唯一、分类数量与依赖有效。只读盘点在 `data/audits/20261008-company-data/inventory.json`；主库哈希不变。
- 同步 README 与 PLAN。建议下一步先固定 30 题现状基线，再单独实现共享、可重建的公司字段/财务事实投影及数据范围/刷新契约，随后按同一快照复测计算与引用，再做中文检索、索引瘦身、备份和模块整理。保持原始证据与冻结研究，不引入新数据库产品或运行框架。

实际验证：`./.venv/bin/python -m pytest -q` → 226 passed（2.92 秒）；题集静态/Pydantic 校验通过，真实题集执行数 0。本次无模型/OpenD 调用、无业务代码改动、无数据库迁移、无 Git 提交。题集准备不等于 19E 评测已完成；上述公司数据层仍是下一阶段建议。

## 2026-10-08：按 Pi 核心边界收口通用财务问答

用户再次报告多轮预算失败，要求从本质解决、参考 Pi 核心设计，并明确允许归档缺指标时调用富途只读接口、必要时由模型编写计算代码。本次继续现有可靠性收口，没有进入新产品阶段、安装扩展、提交 Git 或修改正式研究。

- `agent_context.py` 提供类型化执行记录到模型请求的 transform/convert 边界；`agent_memory.py` 对结果作有界 JSON 投影、剥离重复引用/审计信息，按规则与问题占用分配空间，压缩不能静默丢掉最新工具结果。
- `agent.py` 返回可恢复工具错误、缓存成功调用并限制重复循环；`agent_model.py` 分开单请求输入目标/输出上限与 Turn 总消耗。新问题默认总预算 30,000，既有 Turn 不扩容。实际原请求用量超过旧 12,000，故不能仅靠字符估算修复宣称已解决。
- `analysis_workspace.py` 支持多检索词任一匹配；`agent_data.py`/`agent_tools.py`/`storage.py` 接通按需富途补取、当前 Turn 来源范围与缓存。来源文件归档在股票目录的 agent-sources 下，不写正式 run_sources；SQL、引用校验和显式 Memo 接受当前 Turn 的真实来源。
- `query_workspace.py` 自动把本轮补取资料纳入新快照，旧产物不变；SQLite 错误返回可纠正诊断，受限函数支持推导式、普通循环、解包与 +=，保持进程/权限限制。
- `tool_registry.py` v4、analysis-workspace v4、interactive-research-agent v2：固定公式是快捷路径，其他指标由模型选择 SQL/函数。`allow_network` 新默认 true，但仅允许当前公司财务补取，false 在连接前拒绝。`agent_policy.py` 明确最终引用对象结构；`router.py` 补充合法枚举/字段协议。
- 新增 `tests/test_agent_data.py`，扩充上下文测试；同步 README、PLAN、技术/Agent 规格、Skill 和出处。中文说明为 `docs/25-agent-context-and-financial-tools.md`。

实际验证：

- `./.venv/bin/python -m pytest -q`：226 passed（2.88 秒）。`./.venv/bin/ruff check src tests`：All checks passed。
- 真实 DeepSeek + 主库只读备份的隔离副本：原“中国移动最近三年毛利率平均值”请求五轮完成，20,315 tokens。模型生成 SQL 选取 2023–2025/FY 三条 CNY 记录，再自行生成 Python 推导式计算平均值，报告约 58.62%。主库哈希不变。
- 真实 DeepSeek + OpenD + 无任何归档的临时库：主动补取富途 statements、查询三年营业总收入，六轮完成、19,891 tokens。该次平均值写在最终模型文字中，未执行聚合/函数，故只证明真实补取与查询通路，不能声称计算工具遵循率达标。主库哈希不变。
- 离线集成另验证完整“缺少自定义指标→模拟补取→SQL→函数→引用→显式 Memo”链路、正式 run 不变、跨 Turn 隔离、缓存、离线拒绝和接口失败恢复。真实摘要/代码审计在忽略的 `data/audits/20261008-multiturn/`，临时库已清理，无密钥或隐藏推理。

限制与下一步：真实尝试中先后出现结果丢失后的循环、计算语法和引用协议失败，不能把两次成功 smoke 当作稳定性基准。最终文字仍可能遗漏期间解释或绕过计算工具；富途沿用现有年度/数量/市场覆盖限制。旧显式授权版本不自动升级，版本不匹配需新建问题。下一步建议优先做 19E 小型跨场景质量/成本基线，检验补取、实际计算执行、期间/重复来源、引用和预算；随后根据证据优化中文检索、共享索引及数据保留/备份。19D.2 和 GitHub Actions 继续暂缓。本轮完成后暂停。

## 2026-10-08：修复 chat 首轮预算误拦截

用户报告“中国移动最近三年毛利率平均值”在第 1 轮即 agent_budget_exhausted。只读检查最近 Agent Session：budget_exhausted、iteration=1、used_tokens=0。重建 system 6,392 字符、上下文约 17,351 字符；19D.1 按字符估 token 的请求前检查与 28,000 字符才压缩的旧阈值不匹配。这是实现回归；Router 已成功修复 JSON 并继续，不是最终失败原因。

- `agent_memory.py` 新增统一轻量输入估算（ASCII 0.4、非 ASCII 1.0、每消息开销 32；不是官方 tokenizer），压缩增加 max_input_tokens，按当前剩余预算减输出预留提前触发。保留完整规则/工具 schema、问题与用户补充；优先省略旧观察，尽可能保留最近完整工具结果；持久化工作记忆/回执不删。
- `agent.py` 首轮来源目录不带 metadata，记忆最多两条、每条 1,200 字符与三个来源索引，附省略数量；API 前置检查使用同一估算，失败说明剩余预算与输入估算，零调用/零消耗。预算默认值、未知用量预留、超额回答拒绝和正式研究边界不变。
- 新增 `tests/test_agent_context_budget.py`：真实项目 Skill + 大型历史记忆的 chat→Agent→mock HTTP 回归、剩余预算触发压缩且保留近期尾部数值、真正不足时不调用 HTTP。调整既有输出上限测试为新估算结果。旧 scripted decide 绕过 API 入口，是此前漏测的原因。
- 中文说明见 `docs/24-agent-context-budget-fix.md`，同步 README 与技术规格。未安装依赖、改 schema、提交 Git 或调用真实模型/OpenD。

实际验证：`./.venv/bin/python -m pytest -q` → 216 passed（2.47 秒）；`./.venv/bin/ruff check src tests` → All checks passed。主库只读备份的临时副本中，以原问题/授权进入 API 前置检查，替换 HTTP 为两轮离线占位响应：首轮 14,221 字符/估算 7,430 tokens，次轮 14,283 字符/估算 7,496 tokens，默认预算内 succeeded；真实网络调用 0、主库文件哈希不变、副本自动清理。摘要在 `data/audits/20261008-context-budget/offline-replay.json`。

限制：副本验证只证明预算路径已修复，不证明真实毛利率计算正确或真实模型一定能在 12,000 总额度内完成。估算不是硬费用上限。原 budget_exhausted Turn 不重开，用户重跑原命令创建新 Turn。中文检索、完整 Turn 工作状态与索引优化仍待后续阶段。

## 2026-10-07：阶段 19D.1 最小运行时与可靠性收口

用户在架构审计后要求继续。本次只完成建议第一项可靠性收口，保留已有 MU 数值审查及其他工作区改动；未提交 Git、安装扩展、执行 CI 或进入下一阶段。

- `storage.py` 增加显式事务/嵌套 SAVEPOINT。`agent.py` 将 Attempt 创建、预算进度、成功状态/报告/episode/FTS、Memo/记忆投影分别原子提交；提交失败不会出现成功状态却没有报告，也不会留下孤立 Memo。
- 收口现有 `agent_runtime.py`/`agent_model.py`：Turn/Attempt 生命周期、按 Turn 报告历史、respond/retry、scripted client、冻结授权与累计预算；模型调用前持久化预算预留，已知 usage 后结算，未知用量保留预留；超额 final 不接受。API 输出按剩余预算与保守输入估算限制，不能当作精确账单上限。
- 新增 Session 执行锁与 `ask-interrupt SESSION_ID TURN_ID`；Ctrl+C/普通异常按持久化进度结算，仍有持锁进程时拒绝遗留中断操作。重试恢复用户补充，同 Turn 可复用已成功且完整或具有受控分析产物 ID 的工具结果；普通截断结果不当作完整缓存。
- schema v12 显式修复 legacy Turn 状态、旧计数与 event/trace 的 Turn 关联；模型允许 Memo 来源 legacy Turn ID。历史报告正文、正式研究与完成产物不重写。
- 新增/扩充 `tests/test_stage19_runtime.py`、`tests/test_agent.py`；更新 QueryWorkspace 测试版本契约。新增中文讲解 `docs/23-agent-runtime-reliability.md`，同步 README、PLAN、技术规格及 Agent 文档。Writer 规格调用上限同步已有 `MAX_MODEL_CALLS=15`，本次没有修改该常量；先前历史记录中的 14 保留为当时状态。

实际验证：

- `./.venv/bin/python -m pytest -q`：213 passed，2.41 秒。
- `./.venv/bin/ruff check src tests`：All checks passed。
- `./.venv/bin/wonder-research --root . ask-interrupt --help`：参数帮助正常。
- 离线 scripted demo：waiting_user → succeeded，同一 Turn 两个 Attempt、2 轮、12 tokens；摘要在 `data/audits/20261007-runtime/offline-demo.json`。
- 合成 v9→v12 保留旧报告 JSON、legacy 成功状态与 2 轮/37 tokens；迁移重复运行、外键检查通过。注入 v11→v12 失败时，DDL 与版本号一起回滚。
- 主库只读备份至临时副本：当前实际 v11→v12、重复初始化通过，quick_check=ok、外键错误 0；stocks/runs/sources/run_sources/run_steps/draft_sections/draft_reviews/source_conflicts/company_memos 全表哈希、历史临时报告正文、13 个冻结文件字节均未变。31 条研究保留，主库文件哈希未变。副本已自动清理；摘要在 `data/audits/20261007-runtime/migration-rehearsal.json`（Git 忽略）。

限制：主库仍为 v11，未运行真实 DeepSeek/OpenD；下一次正常业务初始化会执行 v12。未知用量可能占满剩余预算而禁止重试，计数此时不是实际账单；硬崩溃恢复通过遗留状态模拟验证，未覆盖全部操作系统时序；锁沿用 macOS/Linux fcntl，未验证 Windows。记忆中文检索、Turn 工作状态、FTS 去重与备份优化尚未实施。下一步建议按架构审计推进上下文/检索优化；19D.2 上手/面试案例与 19E 评测待用户决定，GitHub Actions 继续暂缓。本阶段完成后暂停。

## 2026-10-07：MU 语义审查数值误报修复

- 用户要求解决 `a8bd82aa-eaac-4169-98be-1acdc2f14fcf` 的 `semantic_review_failed`。新增 `writer._verify_numeric_review`：ROE 阈值计数由可信序列核对；ROE 展示精度须匹配归档字段、期间与来源；债务权益比须核对归档操作数、币种/单位、正文公式和四舍五入。无法核实或真实不一致仍保留 error，不修改评分或草稿。
- 新增 `review-draft --recheck` 离线复核同 hash 的既有模型审查；追加 `semantic-review-v2+offline-numeric-v1` 记录，原审查不覆盖，人工/过期审查不复用。README 与技术规格同步说明。正常模型审查也执行同一数值核对。
- 本次 MU 复核证明 ROE >15% 为 9 个财年、ROE 尾随零不改变值、债务权益比约 0.77 且口径已经写明；4 条 error 均变为带核对说明的 warning，原有 warnings 保留。主库 run 仍为 draft，hash 未改；未运行模型/OpenD/网络，不自动 finish。
- 实际运行：`./.venv/bin/wonder-research --root . review-draft a8bd82aa-eaac-4169-98be-1acdc2f14fcf --recheck` → passed；同 run `validate` → valid=true、无 issues。
- 实际测试：`./.venv/bin/python -m pytest -q` → 200 passed；`./.venv/bin/ruff check src tests` → All checks passed。新增计数、精度、引用、债务比值/口径及过期审查/历史保留回归；现有预算测试改为读取当前 `MAX_MODEL_CALLS`，不再硬编码旧上限。
- 限制：离线核对只处理明确可证明的数值误报，不代替完整财报补证或人工研究判断。MU 的定性资料缺口仍存在，通过验证不代表证据完整。19D 仍待收口，GitHub Actions 仍按用户要求暂不实施。

## 当前状态

- 当前阶段：19C Turn 级 QueryWorkspace、19D.1 最小运行时与可靠性收口已实现；固定研究可靠性收口也已完成。腾讯、中国移动、中国电信的新自然语言研究均通过当前 `validate`，正式报告在隔离副本通过首次和重复 `finish`；三条原 run 均保持 draft，不会自动冻结。
- 业务软件：固定研究新增六组 EvidencePack、Decimal 可信计算、staged Writer、定向修订、DraftReview、schema v6 `draft_sections`/`draft_reviews`、schema v7 `source_page_fts`、schema v8 来源冲突/Agent Scratchpad/记忆索引/分析审计、`review-draft` 和 finish 语义门闸。既有 collect、交互 Agent、Memo、chat 与历史完成报告保持兼容。
- 附记正文与估值：仍未实现。真实 DeepSeek API smoke 已跑通；加入 fundamentals 后 HK.00728 草稿由“1 answered/23 partial”变为“24 answered、6/6 门闸 pass、M1–M6 均有分”。
- writer schema 修复：分组 Prompt、Schema、解析、缓存和修订统一为 `AnswerSection`；非法 JSON 从原证据输入重新生成一次，结构修复仍限一次。修复失败保持 draft，不自动 `finish`。
- 报告层：新完成报告使用 Q01–Q24 完整问题文本、中文标签、精确数值格式和短引用索引；TTY 完成反馈列绝对路径，非 TTY 保持 JSON。
- Writer：默认模板为 `deepseek-staged-v6`；`deepseek-draft-v3` one-shot 仅保留回归。分组 Writer 只接收与该组相关的可信计算，语义审查与 Python 硬校验共享受限修订单元，最终 error 不会被自动 finish。
- 自然语言失败反馈：collect/Writer/存储失败返回 `failed_step`、run_id 与可恢复 session；`validate` 失败返回逐项 issues。TTY 可输入“重试”复用原 run，非 TTY 返回 `ok=false` 和 exit 2。
- Skill：Router 仅接收 Catalog 摘要；`skill-list`、`skill-check` 和 `start --skill` 可用。方法正文/reference 只在确认后的 Writer 或交互 Agent 执行边界加载，实际加载的名称、版本、内容哈希进入 run step 或 Agent event，正文不落库。阶段 19B 新增 Python Tool Registry；交互 `chat` 按所选 Skill 的 `tools` manifest 限定 Agent 可见和可执行的函数。
- 开放问题：既有 Python Agent loop 新增已归档财务检索、可信固定计算，以及 Turn 级隔离财务快照、只读 SQL、受限函数和证据回执。Scratchpad 自动保存可检查的工作状态；长期可信记忆只来自 completed 报告和 `memo-save`，临时报告只作为 provisional episode 索引。
- 可观测性：schema v9 的本地 `traces` / `trace_spans` 记录 Writer、Reviewer、Router 和 Agent 的安全元数据时间树。`trace-read RUN_ID` 与 `agent-trace SESSION_ID` 可直接排查；可选 OTLP exporter 可指向本地 Phoenix 或 Langfuse，未配置时仍只用项目 SQLite。用户可显式设置 `WONDER_TRACE_CAPTURE_CONTENT=preview|full`，让后端显示可见 LLM/工具 I/O；正文不会写入项目 SQLite，隐藏推理与凭据仍不采集。
- 来源核对：修复 `source_conflicts` 的稳定 ID 漏掉 currency 的缺陷。现在同一富途来源、指标和期间的 CNY/HKD 事实会各自持久化，重新写草稿时不会因主键冲突失败。
- Writer 分组覆盖修复：当 DeepSeek 将证据不足误写成空 `answers` 数组时，初始请求和唯一一次 schema 修复都会明确要求 Q01–Q24 的每个分组完整返回；证据不足必须以 `partial`/`missing` 加完整 `gap` 表示。不会伪造答案、放宽证据校验或自动 `finish`。

## 2026-10-06：记忆、存储与 Agent 架构审计（建议阶段，未实施优化）

用户要求核对现有记忆、wonder.db、Agent/代码架构，调研主流方案并提出下一步计划。本次新增 `docs/22-memory-storage-architecture-review.md`，只记录审计与候选计划，不替代 PLAN 的阶段安排；没有修改业务代码、迁移主库、清理历史或调用真实模型/OpenD。

实际检查：

- `./.venv/bin/python -m pytest -q`：190 passed、1 failed（2.53 秒）。失败仍为 `test_model_call_budget_stops_before_fifteenth_call`；当前 Writer 常量为 15，测试/规格要求最多 14，需要先收口契约，不预填全绿。
- `./.venv/bin/ruff check src tests`：All checks passed。
- 主库以 SQLite `mode=ro`/`query_only=ON` 检查：78.59 MiB，实际 schema v9，quick_check=ok、外键检查无错误。当前工作区代码已为 schema v11，包含部分 19D 的 Attempt/respond/retry/按 Turn 报告/model adapter；旧进度不能代表当前全部代码或主库使用状态。
- 页级 FTS 占 65.40 MiB（83.22%）；15,365 条按公司/字节哈希/locator/content hash 去重为 7,351 条。来源获取事件应保留，重复索引可优化。18,673 条 source_conflicts 全为 provider_unverified；Memory、Scratchpad、Memo、analysis 主库表目前为空。
- 内存 SQLite 中文检索实验：unicode61 对样例中的“资本开支”零命中，ROE 命中。隔离合成项目故障注入复现：报告写入失败后 Session/Turn/Attempt 已为成功、报告数为 0；现有项目锁没有替代显式数据库事务。
- 只读统计与隔离实验产物在 `data/audits/20261006-architecture/`，保持 Git 忽略。没有安装额外 Skill/扩展。

建议下一步先完成 19D.1 可靠性收口（事务、预算、进度/中断与迁移），随后分阶段修正现有上下文/中文检索、共享索引/备份覆盖，建立 19E 基线后再做模块整理；完整事实记忆仍留后续阶段。调研来源、具体修改点和验收条件见新增审计文档。这些建议尚未实施，也不宣称已验证真实模型或框架迁移收益。

## 2026-10-06：交互 Agent 内部组织（19C 前置整理）

- 将原 1,321 行 `agent.py` 整理为约 900 行的会话入口和有限循环；新增 `agent_policy.py`、`agent_tools.py`、`agent_memory.py` 与 `agent_errors.py`。公开 CLI 与 Python 入口不变。
- System prompt 改由 `agent_policy.build_system_prompt()` 动态组装：不可变权限/引用/JSON 协议在前，受信任 Tool Registry schema 居中，已选 Skill 的方法正文最后追加。Skill 仍不能创建 Tool、授予权限或覆盖 Python 校验。
- Scratchpad、上下文压缩和可信长期记忆移动到 `agent_memory.py`；Tool handler、参数解析、ticker/run/source/Memo 归属检查移动到 `agent_tools.py`。主循环继续顺序执行受控 Tool，不引入 Pi runtime、并行 Tool、steering queue、多 Agent 或新模型供应商。
- 参考 Pi Agent Core 当前的 loop/context/Tool 分层，但仅采用小模块边界；详细说明见 [docs/20-agent-runtime-organization.md](../../20-agent-runtime-organization.md)。这不是 19C 的 Turn/QueryWorkspace 实现，阶段计划不变。

## 2026-10-06：阶段 19C QueryWorkspace

- schema 升至 v10：新增不可变 `agent_turns` 与 `agent_evidence_receipts`，`analysis_runs` 记录可空 `turn_id` 和 `sql_hash`。每次 `ask`、`ask-followup` 都新建 Turn；v9→v10 为旧 session 写入 `legacy-<session_id>` 并关联既有分析记录，不改写历史事件、报告、Memo 或正式研究。
- 新增 `query_workspace.py`：仅从当前 run 的归档结构化数值构造 `financial_facts` / `source_metadata` SQLite 快照。快照位于 `data/tmp/analysis/<session>/<turn>/<analysis>/`，不含来源全文、正式报告、Memo 或项目主数据库内容；至多 10,000 条事实，写完改为只读。
- v3 `analysis-workspace` Skill 新增 `describe_analysis_schema`、`run_readonly_sql`、`run_analysis_function`、`read_analysis_artifact`。SQL 仅允许一条 `SELECT` / `WITH ... SELECT`，在独立受限子进程的 immutable 只读快照上通过 authorizer、`query_only`、progress handler、10 秒/256 MB/64 KB/400 行限制执行；函数只能处理对应 SQL 行，使用受限 AST 与无项目环境变量的子进程。
- 只有 SQL 结果直接返回 `fact_id` 时，程序才反查真实 `source_id + locator` 为可引用事实；实际返回的来源会写入当前 Turn 的回执。新版 Skill 路径的临时报告引用必须命中同一 Turn、同一来源哈希的回执。SQL/函数错误以稳定码回给模型；重复同一错误或连续三次 QueryWorkspace 错误会停止本 Turn。
- 未实现 19D 的 Attempt、可恢复状态机、重试/补充 CLI、按 Turn 保存临时报告历史或通用 model adapter；没有改动固定 `ResearchRun`、评分、门闸、Writer、`validate` 或 `finish`。未调用真实 DeepSeek/OpenD，也没有生成、改写或冻结正式报告。
- 详细教学说明见 [docs/21-query-workspace.md](../../21-query-workspace.md)。

验证：

- `./.venv/bin/python -m pytest -q tests/test_stage19_query_workspace.py`：5 passed（v9→v10 legacy migration、快照/函数/回执、SQL/AST 拒绝、Turn 级次数限制、可恢复错误后的 fake-model 成功路径）。
- `./.venv/bin/ruff check src tests`：All checks passed。
- `./.venv/bin/python -m pytest -q tests/test_agent.py tests/test_stage18_agent.py tests/test_tool_registry.py tests/test_skill_registry.py tests/test_observability.py tests/test_stage19_query_workspace.py`：38 passed。
- `./.venv/bin/python -m pytest -q`：186 passed、1 failed；唯一失败是已有 `tests/test_stage16_quality.py::test_model_call_budget_stops_before_fifteenth_call` 期待 Writer 在第 15 次调用前抛错，但当前 Writer 未抛错。它与 19C 文件及行为无关，本阶段未混入 Writer 修改。
- `./.venv/bin/wonder-research --root . skill-check analysis-workspace`：通过；Skill v3 解析到六个已注册、capability 兼容的工具。

## 2026-10-06：Langfuse OTLP / Agent Graph 配置

- `WONDER_TRACE_EXPORTER=langfuse` 复用现有 OpenTelemetry exporter；从 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`（或项目的 `WONDER_LANGFUSE_*` 等价变量）在内存中构造 Basic Auth，默认发送到 EU Cloud 的 OTLP trace endpoint，也支持 US、日本及自托管根 URL 或完整 endpoint 覆盖。
- 导出时自动携带 Langfuse v4 的 `x-langfuse-ingestion-version: 4`、`langfuse.observation.type`、trace name 和 Agent `session_id`，使 LLM、Tool、Agent/Chain 能在 Agent Graph 的 Aggregated / Expanded 视图中正确显示与归组。
- `preview` / `full` 内容模式额外映射到 Langfuse observation input/output；SQLite、`trace-read` 和 `agent-trace` 继续只保留摘要。公钥、密钥、Authorization 和隐藏推理不记录、不打印、不写入本地数据库。
- `.env.example`、README、技术规格和 `docs/16-tracing-and-phoenix.md` 已补充配置、清理命令及隐私边界。验证：`tests/test_observability.py tests/test_agent.py` 为 `10 passed`；Ruff 与 `git diff --check` 通过。未使用真实 Langfuse key、未发送外部 trace、未调用 DeepSeek 或改写研究报告。

## 2026-10-06：固定研究真实流程可靠性收口

- 根因：腾讯三次 Q01–Q04 失败均为模型正常 `stop` 后返回损坏 JSON；旧解析器扫描到内部 Statement/Gap，却最终误报 `invalid_section_coverage（实际为空）`。提示要求对象、附带 Schema 却是数组，也造成契约冲突。
- 修复：新增 `AnswerSection`，统一 Prompt/Schema/解析/缓存/修订；损坏外层 JSON 保留真实错误位置。非法 JSON 修复从原 EvidencePack 重新生成，不复制损坏响应。EvidencePack 原文只发送一次，question_chunks 使用引用索引；腾讯首组 prompt 由约 30,700 降至约 21,800 token。
- 综合判断接受精确 G/M ID 映射的无损归一化；DraftReview error 与 Python 确定性校验 error 合并进入最多五单元的修订循环。缺权威支持时只能删除陈述或降级答案并补 gap，不能把 context 改 supports。
- chat 在 collect/Writer/存储/finish 失败时保存 run_id、failed_step 和恢复动作；同一 session 输入“重试”复用原 run、归档来源和成功分组，不重新 collect。
- 真实验收：腾讯 `36312547-ca74-4e2d-9b6e-a8b98e9e22a6`、中国移动 `eac6fecc-3e80-4524-af4b-c4b3ae677c7a`、中国电信 `81f8aa26-41f2-41f7-a445-65472ecc8cc5` 均完成 24 Q / 6 Gate / 6 Moat 并通过 validate。三条原 run 保持 draft。
- 隔离 finish：三条 run 分别复制到 `data/tmp/fixed-flow-check/freeze/<run_id>/`，首次与重复 finish 均通过，生成 company.md、research.json、quality-review.json；draft/review hash、产物哈希与覆盖数量一致。
- 用户原 `uv run --extra observability` 入口对三条 run 分别执行 `validate`，均返回 `ok=true, valid=true, issues=[]`；受限环境使用 `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache`，避免写用户全局 uv cache。
- 验证：`.venv/bin/python -m pytest -q` → `174 passed`；`.venv/bin/ruff check src tests` → `All checks passed!`。详细说明见 `docs/18-fixed-flow-reliability.md`。

已知限制：自动审查和程序校验不能证明研究结论正确；三份原草稿仍需用户检查 warning、数值口径和判断后再决定是否正式 finish。Reviewer 输入仍较大，后续可在不删除已引用证据的前提下继续压缩。

## 2026-10-06：Writer 空分组回答修复

- `writer._answer_messages()` 现在将“每题恰好一次、`answers` 不得为空、缺证据仍返回 `partial`/`missing` + `gap`”作为分组 Writer 的显式约束。
- `writer._section_repair_messages()` 在修复 `invalid_section_coverage` 时收到该分组的精确 QID 列表，明确禁止返回空数组，并说明补缺口不属于新增事实；仍只保留一次受控修复。
- `tests/test_stage16_quality.py` 新增回归：首轮 `{"answers": []}` 后，修复响应覆盖 Q01–Q04 时可完成整条 staged Writer 路径，并断言修复提示含完整覆盖契约。
- 验证：`UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest python -m pytest -q`：163 passed；`UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with ruff ruff check src tests`：All checks passed。未调用真实 DeepSeek，也未改写、冻结任何研究报告。

## 2026-10-06：来源核对主键修复

- `source_policy.reconcile_sources()` 的 `SourceConflict.id` 现在同时纳入 provider source、metric、period、currency 和 official source；此前漏掉 currency，导致中国电信 run 的同指标同期间 CNY/HKD 事实得到相同 SQLite 主键。
- `tests/test_stage18_agent.py` 新增双币种回归：同一 field/period 的 CNY 与 HKD 都必须写入，且 ID 不相同。
- 验证：`UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest python -m pytest -q`：162 passed；`UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with ruff ruff check src tests`：All checks passed。
- 针对真实中国电信 run `e5d20c8c-2bd5-4fa3-a8d3-db873a79d52b` 的只读重算得到 `facts=1174`、`unique_ids=1174`；修复前为 1174 / 895。尚未再次调用 DeepSeek 或保存正式草稿。

## 已准备的交接文件

README.md、AGENTS.md、PLAN.md、docs/research-spec.md、docs/technical-spec.md、docs/teaching-contract.md、docs/provenance.md、docs/09-agent-layer.md、docs/15-open-question-agent.md、skills/company-analysis-method/、skills/interactive-research-agent/、skills/analysis-workspace/ 和本文件。

## 下一步：阶段 19D 最小运行时、上手与面试案例

三家电信新草稿仍保持 draft，用户可以继续验收；这不阻塞下一阶段。19D 将在 19C 的 Turn 边界上增加 Attempt、受控状态迁移、失败/等待恢复、按 Turn 保留临时报告和最小 model adapter；随后补 Quick Start、离线 CI 与面试案例。跨场景评测基线仍在 19E，记忆专项评测、Pi 和多 Agent 不提前引入。

## 阶段 19B 记录

阶段：19B，Skill 驱动的受控工具路由。

完成日期：2026-10-05。

实现内容：

- 新增 `tool_registry.py`，集中登记稳定工具 ID、描述、Pydantic 参数 schema、适用 capability、默认可见状态和 Python handler；无 handler 的工具不会出现在模型 schema。
- `skill.json` 增加可选 `tools`。Catalog 拒绝未知/重复工具、method Skill 声明工具、工具与 capability 不匹配或无已注册 handler 的情况；审计元数据记录每个 Skill 声明的工具。
- `chat` 确认后的交互执行只向 Agent 提供 capability 基础工具与所选 Skill grants 的合并集合，执行时用同一 allowlist 再校验。未授权工具调用在触及数据库或 handler 前返回 `unauthorized_tool`。
- 将 `analysis-workspace` Skill 绑定到 `interactive_research`，通过 manifest 开放财务查询、可信计算、只读数据集、受限代码执行和结果读取。原 `interactive-research-agent` Skill 正文和 manifest 未改；直接 `ask` 仍支持原有完整工具集合。
- Agent session 记录 Skill audit、工具 allowlist 和 registry version；follow-up 重载并检查哈希和授权，权限或 Skill 变化时停止。旧审计记录缺少 `tools` 字段时按空声明兼容，旧 run 的 Writer Skill 恢复也保持兼容。
- Fixed Research 的 EvidencePack、固定写作步骤、校验和 finish 门闸没有改变。Agent loop 继续在可用工具内自主决定调用顺序，不新增 LangGraph、多 Agent 或任意 shell。

验证：

- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest python -m pytest -q tests/test_tool_registry.py tests/test_skill_registry.py tests/test_agent.py`：21 passed。
- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest python -m pytest -q`：161 passed。
- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with ruff ruff check src tests`：All checks passed。
- `./.venv/bin/wonder-research --root . skill-check analysis-workspace`：通过，五个工具都已注册且 capability 兼容。
- 没有调用真实模型、OpenD 或证券账户接口，也没有生成/修改/冻结任何正式研究报告。新增测试包含 Session follow-up 授权恢复；真实 DeepSeek Router 是否稳定选择该 Skill 尚未做在线 smoke。

详细实现说明和权限边界见 [docs/17-skill-driven-tool-routing.md](../../17-skill-driven-tool-routing.md)。

## 阶段 19A 记录

阶段：19A，DeepSeek / Agent 调用轨迹与可选本地 Phoenix。

完成日期：2026-10-05。

实现内容：

- schema v9 新增 `traces`、`trace_spans` 与按 run/session 的索引。顶层 trace 关联 `RUN_ID` 或 `SESSION_ID`；child span 保存 parent、类型、状态、时序和受控属性，失败仅保存错误类型/码。
- 新增 `observability.py`。Writer、Reviewer、Router、Agent 的模型调用和 Agent 工具调用均围绕标准 `llm`、`tool`、`chain` span；默认完整 prompt、问题、模型输出、工具结果、隐藏推理、认证头、API key 与异常原文都会过滤。输入/参数/结果仅以长度和 SHA-256 摘要出现。
- 新增只读 CLI `trace-read RUN_ID`、`agent-trace SESSION_ID`，输出保持既有 JSON stdout 契约。原有 `agent_events`、Scratchpad 与 `ask-status` 不改变。
- Phoenix 兼容属性：LLM span 另发 OpenInference 标准 `llm.model_name`、`llm.token_count.*`，Agent 根 span 使用 `AGENT`，Writer/Router 使用 `CHAIN`。Agent 另有 `agent.iteration`、`agent.decision`、`tool.*` 和 `skills.selected` 节点，能展示实际工具轨迹。默认仍不发送正文；经用户要求，新增 `WONDER_TRACE_CAPTURE_CONTENT=preview|full`，仅向 OTLP/Phoenix 导出可见 LLM input/output、工具 arguments/result，绝不写入 SQLite、导出隐藏推理或凭据。
- 增加 `observability` optional dependency group（OpenTelemetry API、SDK、OTLP/HTTP exporter）。`WONDER_TRACE_EXPORTER=otlp`、`WONDER_OTLP_ENDPOINT` 和 `WONDER_TRACE_PROJECT` 可将同一安全属性发送到 Phoenix 或其他 OTLP/HTTP collector；项目名同时作为 `service.name` 与 `openinference.project.name` 发出，确保 Phoenix 归入指定项目。没有依赖或 collector 不可用仍保留本地 trace，不影响研究/Agent 的业务结果。
- 新增 `docs/16-tracing-and-phoenix.md`，同步 README、计划和技术规格；Phoenix 运行于项目外部，本项目不依赖其运行或保存其数据。

验证：

- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest python -m pytest -q`：151 passed（2026-10-05）。新增覆盖本地 trace 的 run 关联、CLI、敏感正文/密钥不落库、Agent tool span 与 v1→v9 migration。
- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with ruff ruff check src tests`：All checks passed。
- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache uv lock`：已更新 `uv.lock`，锁定 optional OpenTelemetry 依赖。
- `WONDER_TRACE_EXPORTER=otlp ... uv run --extra observability python -c ...`：SDK/exporter 初始化成功；该 import 验证本身未启动 Phoenix、未发送真实 DeepSeek 请求或改写研究产物。
- 中国电信 `write-draft` 真实运行（2026-10-05）：本地 trace `f5d5c717…` 记录 9 个成功 LLM span 后以 `evidence_outside_pack` 失败；未调用 `finish`。随后补充 Phoenix 使用的 `openinference.project.name` resource 属性，并以 optional exporter 读取该属性确认值为 `wonder-stockresearch`。
- `tests/test_observability.py tests/test_agent.py`：9 passed；完整离线测试：153 passed；`ruff check src tests`：All checks passed（补充标准 OpenInference LLM/Agent 属性、正文采集开关及 Agent iteration/decision span 后）。

已知限制：本阶段不追溯历史调用；`trace-read` 对未调用 Writer/Reviewer 的 run 返回空数组。自然语言 Router 的分类是在确定 run/session 前发生，因此保留为独立 trace，不能伪造为后续正式研究或 Agent 的父节点。Phoenix 只展示启动 exporter 后的新 trace，需由用户在本机另行启动；本次未做真实 collector 联通 smoke。

下一步：由用户决定是否在本机启动 Phoenix 完成一次非敏感的真实 Agent/Writer smoke，或转入阶段 19 的 SEC XBRL、专题数据源和 PDF/OCR 基准。

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

已知限制：本条记录反映阶段 17 完成当时的边界。阶段 18 已启用 `analysis_workspace`，但仍不提供 shell、网络、真实 SQLite 写入或新的外部数据能力。

## 阶段 18 记录

阶段：18，固定研究数据优化、开放问题 Agent 工作记忆与受限临时计算。

完成日期：2026-10-04。

实现内容：

- 新增 `source_policy.py`：分离“获取顺序”和“证据权威顺序”，为每个来源和 EvidenceChunk 保存 `source_rank`、`extraction_method`、`supports_eligible`、`reconciliation_status`。EvidencePack 的稳定排序改为可用性、题目匹配、权威、结构化程度、期间匹配和 locator；搜索发现记录无法成为核心 supports。
- `futu_fundamentals.statements` 成为唯一财务报表采集路径。财务接口按 `next_key`、每页 50 条分页，每类报表去重后最多保留 20 个年度期。`market` 仅采集行情快照，standard `collect` 不再请求 valuation/consensus。
- 同一 run 出现富途与官方结构化数值时，程序按字段、期间、币种记录 `matched`、`provider_unverified` 或 `conflict`。官方披露优先；未在 ResearchDraft `conflicts` 中以两个 source_id、说明和 `resolved=true` 明确处理的冲突会使 `validate`/`finish` 返回 `unresolved_source_conflict`。
- schema v8 新增 `source_conflicts`、`agent_scratchpads`、FTS `memory_entries` 与 `analysis_runs`。follow-up 加载 Scratchpad、最近事件和最多五条相关记忆；达到上下文预算 70% 时以可检查的结构化摘要压缩，完整 events 仍保留。completed report 与 `memo-save` 才写可信长期记忆；InteractiveReport 只写 `provisional` episode。
- 新增 `AnalysisWorkspace`、`analysis_workspace` capability 和同名 Skill。优先通过 `run_trusted_calculation` 回答；必要时导出最小只读 JSON，执行受限 Python AST，禁止 import、属性、动态执行、文件、SQLite、网络、shell 和环境变量访问。子进程默认无 API Key，限制十秒、256 MB、64 KB 输出和每 session 三次执行，并保存输入/代码哈希、来源和结果摘要。
- 新增 `memory-search`、`memory-read`；`ask-status` 返回 Scratchpad、实际使用的记忆、可信计算和分析执行审计。同步更新 README、研究规格、技术规格、富途能力说明、Agent 层、计划与 [docs/15-open-question-agent.md](../../15-open-question-agent.md)。

验证：

- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with pytest python -m pytest -q`：149 passed（2026-10-04）。
- `UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache PYTHONPATH=src:. uv run --no-project --with ruff ruff check src tests`：All checks passed。`git diff --check`：通过。
- 对已有三份 draft 执行只读 `validate`：中国移动 `fe6ecce2…`、中国联通 `e491dee8…`、中国电信 `e5d20c8…` 均返回 valid。直接从其归档来源重算：三家净利润同比各有 9 个相邻年度观察、ROE 各覆盖 10 年、Capex/收入各覆盖 5 年；没有调用 DeepSeek、OpenD、`write-draft` 或 `finish`。
- `skill-check analysis-workspace`：能力 Skill 通过 Catalog、依赖和 Capability Registry 检查，显示 `available=true`；没有加载未选择 Skill 的正文或调用模型。
- 新增离线覆盖：富途财务分页/去重与唯一调用路径、standard collect 不采集估值、EvidencePack 稳定排序与搜索发现不可做 supports、官方数值匹配/缺失/冲突、冲突阻止 validate、Scratchpad 恢复/压缩、FTS 记忆隔离与 provisional 边界、固定计算优先、分析 AST 越权拒绝、资源审计与每 session 次数上限、schema v7→v8 迁移。

已知限制：本次没有调用真实 DeepSeek/OpenD，也没有新建或 finish 三家电信公司的正式 run。已完成现有归档的固定计算与 validate 回归；后续真实 Agent 验收仍应分别检查中国移动净利润增长/ROE、三家公司 Capex/收入、联通和电信的债务现金流与分红差异，并记录引用完整率、模型调用、恢复和分析审计。OCR、通用联网搜索、SEC XBRL、Pi runtime、多 Agent 与容器/E2B 不在阶段 18 范围。

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

新增/修改文件：`src/wonder_stockresearch/fixed/fixed_quality.py`、`models.py`、`storage.py`、`research.py`、`writer.py`、`cli.py`、`tests/test_stage16_quality.py` 及相关回归测试、`docs/13-fixed-research-quality.md`、README、PLAN、技术规格和本文件。

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

新增/修改文件：`src/wonder_stockresearch/fixed/questions.py`、`src/wonder_stockresearch/fixed/reports.py`、`src/wonder_stockresearch/fixed/writer.py`、`src/wonder_stockresearch/chat.py`、`src/wonder_stockresearch/cli.py`、`tests/test_writer.py`、`tests/test_chat.py`、`docs/07-deepseek-writer.md`、`docs/10-standalone-natural-language.md`、`docs/12-question-alignment-and-validation-feedback.md`、`docs/technical-spec.md`、`PLAN.md`、`README.md` 和本文件。

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

新增/修改文件：`src/wonder_stockresearch/fixed/reports.py`、`src/wonder_stockresearch/cli.py`、`tests/test_reports.py`、`tests/test_storage.py`、`tests/test_chat.py`、`docs/11-report-presentation.md`、`docs/technical-spec.md`、`PLAN.md`、`README.md` 和本文件。

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
- `PYTHONPATH=src:. uv run --no-project --with ruff ruff check src/wonder_stockresearch/fixed/writer.py tests/test_writer.py`：All checks passed。

已知限制：本次只修复结构校验失败，不自动修复非法 JSON、网络错误或事实/引用错误；真实保利物业 run 需要在用户配置 DeepSeek API 后重新执行 `write-draft` 验证模型实际修复率。

## 阶段 11–12 记录

阶段：11–12，进度事件与独立自然语言入口。

完成日期：2026-09-22。

新增/修改文件：`src/wonder_stockresearch/progress.py`、`src/wonder_stockresearch/router.py`、`src/wonder_stockresearch/skill_registry.py`、`src/wonder_stockresearch/chat.py`、`src/wonder_stockresearch/models.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/fixed/research.py`、`src/wonder_stockresearch/fixed/writer.py`、`src/wonder_stockresearch/agent.py`、`src/wonder_stockresearch/cli.py`、`src/wonder_stockresearch/symbols.py`、`tests/test_chat.py`、`tests/test_symbols.py`、`README.md`、`AGENTS.md`、`PLAN.md`、`docs/09-agent-layer.md`、`docs/10-standalone-natural-language.md`、`docs/technical-spec.md`、`skills/company-research/SKILL.md` 和本文件。

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

新增/修改文件：`src/wonder_stockresearch/futu_fundamentals.py`、`src/wonder_stockresearch/fixed/research.py`、`src/wonder_stockresearch/fixed/writer.py`、`src/wonder_stockresearch/cli.py`、`tests/test_fundamentals.py`、`docs/08-futu-data-capabilities.md`、`docs/research-spec.md`、`docs/technical-spec.md`、`README.md` 和本文件。

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

新增/修改文件：`src/wonder_stockresearch/fixed/writer.py`、`src/wonder_stockresearch/envfile.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/fixed/research.py`、`src/wonder_stockresearch/cli.py`、`.gitignore`、`.env.example`、`tests/test_writer.py`、`tests/test_envfile.py`、`tests/test_run_steps.py`、`docs/07-deepseek-writer.md`、`README.md`、`PLAN.md`、`AGENTS.md`、`docs/technical-spec.md` 和本文件。

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

新增/修改文件：`src/wonder_stockresearch/fixed/research.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/fixed/reports.py`、`src/wonder_stockresearch/cli.py`、`tests/test_review_and_backup.py`、`docs/06-review-and-maintenance.md`、`README.md`、`PLAN.md`、`docs/technical-spec.md` 和本文件。

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

新增/修改文件：`skills/company-research/SKILL.md`、`opencode.json`、`src/wonder_stockresearch/fixed/research.py`、`tests/test_evidence_as_of.py`、`tests/test_sources.py`、`docs/05-opencode-and-tools.md`、`docs/research-spec.md`、`docs/technical-spec.md`、`README.md`、`PLAN.md` 和本文件。

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

新增/修改文件：`src/wonder_stockresearch/market.py`、`src/wonder_stockresearch/disclosures.py`、`src/wonder_stockresearch/fixed/research.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/cli.py`、`pyproject.toml`、`uv.lock`、`tests/test_run_steps.py`、`tests/test_collect.py`、`docs/04-data-connectors.md`、`README.md`、`docs/technical-spec.md`、`docs/provenance.md` 和本文件。

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

新增/修改文件：`src/wonder_stockresearch/sources.py`、`src/wonder_stockresearch/fixed/research.py`、`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/models.py`、`src/wonder_stockresearch/cli.py`、`pyproject.toml`、`uv.lock`、`tests/test_sources.py`、`docs/03-sources-and-evidence.md`、`README.md` 和本文件。

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

新增/修改文件：`src/wonder_stockresearch/storage.py`、`src/wonder_stockresearch/fixed/research.py`、`src/wonder_stockresearch/cli.py`、`src/wonder_stockresearch/models.py`、`src/wonder_stockresearch/fixed/reports.py`、`tests/test_storage.py`、`docs/02-storage-and-history.md`、`README.md`、`PLAN.md`、`docs/research-spec.md`、`docs/technical-spec.md` 和本文件。

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
