# 技术规格 — 现行契约

本文件描述当前技术约定，供开发与验收使用。业务含义由 [研究规格](research-spec.md) 定义，安装与使用见 [README](../README.md)，设计参考见 [出处说明](provenance.md)。个人开发计划与历史记录不随仓库分发。

## 1. 模块与依赖

项目独立运行：Python >=3.11、Pydantic 2、sqlite3/argparse、requests/pypdf/futu-api；开发使用pytest/ruff，实际依赖版本以pyproject.toml和uv.lock为准。入口为 `wonder-research = wonder_stockresearch.cli:main`，兼容 `python -m wonder_stockresearch`。不依赖OpenCode、Pi CLI或其他外部宿主。

| 代码区域 | 职责 |
| --- | --- |
| cli.py / chat.py / router.py / skill_registry.py | CLI、自然语言确认与受控路由、Skill加载 |
| fixed/ | 固定研究、证据包、Writer/Reviewer、校验与正式报告 |
| agent.py / interactive/ | 公开问答入口、Session/Turn/Attempt、共用预算/工具/答案接收、legacy循环 |
| interactive/pi/ / pi_runtime/ | Python/Pi协议与项目自带Node运行时包装 |
| models.py / storage.py / company_data.py / company_parser.py | Pydantic契约、主库、公司事实投影与解析 |
| sources.py / disclosures.py / market.py / futu_fundamentals.py | 原文归档、公告发现、只读行情与基本面 |
| model_request.py / financial_calculations.py / observability.py | 公共Python模型传输、共用计算、安全trace |

Python权威管理领域规则、权限、来源/回执、计算、累计预算、状态与事务。legacy默认路径由Python循环调用模型；Pi显式预览由项目自带Node >=22.19进程调用锁定的pi-ai/pi-agent-core 0.83.0，包装源码为TypeScript，运行构建后的runner.mjs。固定Writer和Router保留Python实现。Pi协议为受Pydantic校验的版本化JSONL，先握手再有序请求；致命业务错误终止bridge/Node。Pi turn不等于用户目标AgentTurn。

普通函数、小模块、直接SQL，不增加ORM、仓储接口、DI容器、插件框架、多Agent或LangGraph。编排演进门槛由本地开发计划另行约定。导入模块不联网或建表；项目根用 `--root` 指定或当前目录，必须有本项目pyproject标识。

## 2. 数据模型与当前默认值

外部业务输入用Pydantic严格校验，拒绝未知字段。ID为UUID；ticker为HK.五位数字或US.规范大写标的，拒绝路径字符和 `..`；SYNTHETIC仅离线fixture。UTC持久化，显示时标明时区。

Source记录身份、类别、原始URL/endpoint或导入标签、发布日期精度/时区、获取时间、哈希、归档路径、提取状态与provider元数据。EvidenceRef含source_id、locator、supports/refutes/context关系。Statement区分fact/inference/assumption，NumericFact保留Decimal文本、单位、币种、期间、口径与引用。

ResearchDraft含24问、6公司门闸、6护城河维度、反证、冲突、结论及复评解释。answered的gap必须null，partial/missing必须完整Gap；分数和质量派生逻辑见研究规格。模型不得输入总分或valuation/target_price/action。DraftReview绑定草稿哈希、检查覆盖与结构化问题，不保存隐藏推理。精确字段/schema以models.py为执行契约，改其语义必须同步对应规格与行为测试。

下表是可机器核对的现行数值；`scripts/dev_check.py`从源码AST读取默认值/常量，与本表比较，不导入业务模块或读取环境凭据。

| 契约键 | 当前值 | 适用范围 |
| --- | --- | --- |
| token_budget | 60000 | 新InteractiveQuestion累计预算；允许256–60000 |
| max_iterations | 10 | 新问题决策轮数；允许1–10 |
| input_token_limit | 16000 | 新Turn单轮输入上限；允许1024–16000 |
| output_token_limit | 4096 | 新问题输出配置；允许64–8192，具体请求还受适配器/剩余额度限制 |
| wonder_schema | 14 | 主库代码支持版本，不表示用户主库已迁移 |
| company_schema | 1 | 公司投影库代码支持版本 |
| writer_max_calls | 15 | 一次固定Writer尝试总调用上限 |
| sql_attempts_per_turn | 6 | QueryWorkspace SQL尝试上限 |
| function_executions_per_turn | 3 | 受限函数实际执行上限，执行失败也计入 |
| financial_record_limit | 20 | 固定采集每类去重年度期上限 |

新问题默认runtime为legacy，显式 `WONDER_AGENT_RUNTIME=pi` 使用预览；thinking默认disabled，Python模型传输温度默认0.2，配置可覆盖。直接ask默认context为latest_completed_report；新chat开放问题使用company_latest。allow_network默认true，显式false在打开连接前拒绝补取。

预算、运行时、授权与来源范围在Turn创建时固化。历史Turn保留原值；缺新增输入/输出字段时沿用适配器旧回退（8000/4096），不批量改写历史预算。legacy目前消费冻结输入上限，但输出仍受其4096及公共请求限制；不能把可配置字段当作两运行时完全相同的行为。retry/respond不刷新预算。真实模型执行许可和次数账本独立于token配置，见PLAN。

## 3. SQLite、文件与版本

- `data/wonder.db`：来源身份、正式研究、run_steps、草稿分组/审查、chat、Session/Turn/Attempt、回执、分析审计、记忆与trace。
- `data/company.db`：source_batches、company_profile_facts、financial_facts；所有公司共用，通过batch关联ticker。它是可重建事实投影，原始归档不迁移。
- 原始字节是资料事实源；SQLite中JSON为草稿/报告权威内容，research.json为冻结快照，Markdown是渲染视图，手改不回写数据库。

来源存 `data/stocks/<ticker>/sources/`；交互补取存 `agent-sources/<session>/<turn>/`。相同字节可复用文件，不合并不同获取事件的Source身份。正式run目录用创建时间和完整run_id固定命名。数据库内路径相对项目根，导入资料归档到data后才能正式引用。

只做显式递增schema迁移，不每次启动重建。历史内容/ID/成功报告/冻结产物保留；新代码支持版本见上表，不猜测本机主库当前版本。foreign_keys=ON、busy_timeout=5000；不开通用事件溯源或任务队列。迁移细节查storage._MIGRATIONS及相应教学章。

run_steps记录步骤、attempt、状态、输入哈希、受控输出引用、provider/model、时刻、错误和安全metadata，不是大段正文或隐藏推理仓库。草稿分组按输入hash复用；审查与草稿hash不符就失效。

公司投影按source/hash/解析器版本幂等导入、整批事务，失败批次不返回事实；partial和未知元数据保留诊断。rebuild经临时库成功后才替换。查询不默认把所有历史批次折叠为最新：人工CLI语义为all_indexed_sources，Agent则按Turn快照选择。每类数据选择当前有效来源版本，旧Turn不静默换新来源。

## 4. CLI契约与报告呈现

共同参数 `--root PATH`；结构化正文经UTF-8 JSON `--input PATH` 或 `--input -` stdin传入，不用shell拼接。脚本/管道stdout为 `{ok,data,issues}`，issues总为数组；正常/仅warning exit0，校验拒绝exit2，I/O/provider失败exit1。进度/日志只到stderr，长任务可用 `--quiet`。

TTY的finish/chat正式完成显示中文摘要、质量/分数和绝对产物路径；管道保持JSON。新报告显示完整Q文本、中文枚举、保留Decimal精度的千位分隔、短引用及完整来源索引；不改变历史冻结报告。

| 命令组 | 现行行为 |
| --- | --- |
| validate-packet / render-packet | 离线结构校验/未归档示例；render输出限data内，不宣称原文已核验 |
| watch add/remove/list | 仅本地手动清单，不删研究历史 |
| start TICKER [--as-of ISO] [--baseline RUN_ID] [--skill NAME] | standard草稿；baseline必须同公司已完成且不晚于新run；无附记/专题启动开关 |
| status / history / draft-read / review-read / trace-read | 有界只读状态、历史、草稿/审查与安全调用树 |
| draft-preview | data/tmp内写未定稿预览，不改变run |
| source-add / sources / source-read | 归档指定file/url，计算真实hash/取得时间，分段读原文 |
| collect [--kind all或market或fundamentals或filings] | 只读采集并按子任务登记成功/失败；market不重复取财报 |
| save-draft / validate / finish / resume | 保存、校验、显式冻结；resume仅failed转draft，不重放网络请求 |
| compare RUN_ID / backup --output PATH | 与baseline比较，或向data/backups新目录写一致快照 |
| write-draft / review-draft | 归档证据写作与审查，细节见第8节 |
| skill-list / skill-check | Catalog与manifest/依赖/授权检查 |
| company-data sync/rebuild/status/fields/read | 离线索引与有界检查，不自动联网 |
| ask / ask-followup / ask-respond / ask-retry / ask-interrupt / ask-status | 交互目标与恢复，见第10节 |
| agent-trace / memory-search / memory-read | 只读调用轨迹与可信记忆检查 |
| memo-save / memo-list / memo-read | 用户显式保存及公司主Memo/历史读取 |
| chat [--text TEXT] [--session-id ID] | 独立自然语言会话，见第11节 |

参数形状和完整选项由当前 `--help` 与Pydantic定义给出，不复制一份容易过期的全量schema。source-read默认120行、最多300行/20000字符；company-data人工分页每表最多200行。未知命令/输入给字段位置和说明。collect至少一个子任务成功则返回成功及各项issues；全部failed/blocked返回失败，不把部分成功当作完整覆盖。

## 5. 核心服务、事务与冻结

CLI做解析/序列化，业务在服务函数。所有业务文件操作限data，路径经过项目根/公司范围检查；不操作旧项目。网络期间不持项目写锁。

finish：项目写锁 → 读取并校验 → 原子生成未完成run产物 → 短事务写completed/hash。生成后提交前崩溃仍未完成，可安全重建；completed重试只核对/返回原产物，hash不符报artifact_mismatch，不覆盖。普通研究校验失败保持draft；存储/渲染异常明确报告，不能声称状态已保存。

标准研究freeze包含company.md/research.json、适用的quality-review.json和baseline review.md。compare对任一侧null给不适用差值，schema/口径不同不算分差。备份在锁内用SQLite backup API取得双库一致快照，复制归档来源、完成产物和登记的分析快照/结果，含manifest/hash、missing/incomplete与恢复说明；不覆盖已有目录。无company.db兼容旧备份。

macOS/Linux用fcntl锁及5秒超时；Windows非本版支持目标。Session执行锁贯穿问答，项目锁只保护短写；最终报告、状态、episode/FTS等在明确事务中提交。

## 6. 网络、时间与财务口径

下载只接受HTTP(S)，阻止公开URL重定向到私网/回环；最多5次重定向、50MiB文件、连接10秒/读取90秒。网络或429/5xx最多2次尝试，Retry-After等待不超过60秒；其他4xx不自动重试。不执行网页脚本/宏或自动安装OCR，提取失败保留原文与诊断。

OpenD为独立只读连接配置（默认127.0.0.1:11111）；只使用OpenQuoteContext，不读账户或交易接口。SEC需用户配置WONDER_SEC_USER_AGENT，不可用只标该通道blocked。公告默认回看5年，从相关候选选择最近5份年报及3份中期/季度报告，实际可取得覆盖必须逐份核对。

财报由fundamentals.statements单一路径按num=50/next_key分页，去重年度上限见第2节；营收构成上限10年度/最近3中期。记录数不等于连续完整财年。provider原字段/值、period、币种、会计准则、单位/倍率和来源时间分别保留；缺失不猜，连接期间不能靠数组下标。

as_of资格、available_from与observed_at的区别只在 [研究规格第6节](research-spec.md#6-来源与陈述) 定义。company_latest不是严格历史时点承诺，不能因旧年度今天可得就称当时可用。官方结构化核对的matched/provider_unverified/conflict按研究规格处理，未解关键冲突不freeze。

## 7. 测试与交付

离线测试用虚构/明确标注归档及scripted/fake模型，替换网络/OpenD，导入SDK不建立连接。覆盖结构、引用/时间、预算/授权、持久化/迁移、恢复/幂等、历史冻结、公司隔离、计算与故障回滚。

开发流程与分层检查见 [README 的开发与验证说明](../README.md#开发与验证)。单测证明相应契约，不证明真实LLM稳定理解、选工具或解释经营原因；真实评测另获额度，固定输入/来源/运行时/预算并逐项核验。工程结果、执行状态、研究质量分别记录，不以日志中的completed计正确率。

## 8. 固定Writer与审查

write-draft默认staged：六组Answers → 综合判断 → DraftReview → 有界修订 → 最终审查/校验。分组统一AnswerSection对象，不从损坏JSON内部片段捞答案；成功分组按输入hash恢复。总调用上限见第2节，最多5个单元各修订一次；最终仍失败保持draft。one-shot用于回归，不绕过新standard run的审查门闸。

Writer只基于已归档证据；ticker/as_of/schema由Python管理，不让模型造source_id/locator或覆盖可信计算。完整Q定义由fixed/questions.py与报告共用。DraftReview只读定义、当前内容、实际引用片段和可信计算，不能直接改草稿；新standard run必须有matching passing review。warning可归档，error阻止完成。

`review-draft --recheck`用现有来源离线核对同hash模型审查中可识别的ROE计数/精度与标明公式的舍入误报（浮点导入容差1e-8），追加记录，不伪装新模型调用。人工/过期审查不复用，不能证明的error继续保留。

凭据仅环境或项目.env读取，不打印/落库；envfile不覆盖已设环境。公共模型默认见第2节，实际配置优先。writer元数据仅安全用量、模板/输入hash、finish_reason和受控输出引用。写作不自动检索或finish。历史真实结果查PROGRESS，不在规格保留“本环境尚未调用”等时效性断言。

## 9. Skill、工具和观测边界

项目Python读取skills/*/SKILL.md及可选skill.json；Catalog只含name/description/path/kind/version/适用能力/可用性/hash，Router只看摘要。执行时按需加载正文/reference，资源限Skill目录；重名、越界、循环、未知capability/tool或无handler拒绝。

纯method不授予权限；capability Skill只组合Python已注册函数。Tool Registry统一Pydantic参数、说明、适用能力、handler和授权，模型schema与执行allowlist一致。Turn固化Skill摘要/hash、registry和工具集合；恢复继续核对。固定Writer不用动态工具循环。直接ask兼容工具集与chat选择的授权不同，不能直接当作同入口A/B。

trace默认保存模型/工具种类、token、长度/hash、时刻、关联ID和受控错误；不保存秘密、隐藏推理或完整请求/工具结果。显式preview/full只将可见I/O送到用户配置的OTLP/Phoenix/Langfuse exporter，SQLite仍保留安全摘要。exporter不可用不影响核心业务，凭据不持久化。详见 [README 的可选观测配置](../README.md#可选pi-与-langfuse)。

## 10. 独立交互Agent、数值回答与公司Memo

Session为长期对话容器；Turn为一次目标及冻结授权/预算/来源；Attempt为一次执行/补充/重试。成功报告按Turn不可覆盖。

- ask创建首Turn，ask-followup创建新实质目标Turn。
- ask-respond只补waiting_user的指定Turn，ask-retry只恢复failed/interrupted；新建Attempt，累计预算/授权不重置。
- ask-interrupt仅结算无存活持锁进程的遗留running，重试不盲目重放成功工具；budget_exhausted/succeeded不能重试覆盖。
- chat执行前关联Agent Session/Turn，等待/失败分别恢复对应Turn。

每次请求预留预算，实际usage结算；usage未知保留保守预留，不能当免费重试。输入估算为启发式，保留规则、完整问题、紧凑最新结果；工作清单从当前Turn执行记录恢复，含成功输入/analysis_id、要求/覆盖、错误与待办，不把摘要当额外已核验事实。

company_latest固定已登记Source/解析器版本，新Turn可复用历史补取；fetch_financial_data只补当前公司获准数据集，不改run_sources。字段位置未知先跨授权dataset检索，已知字段可直接取值。目录明确覆盖、展示、截断和分页，不能作为事实引用；实际回执及来源hash核对后才可引用。

QueryWorkspace将当前范围导出隔离只读SQLite，不给模型主库SQL。最多10000事实，SQL最长12000字符/400行，单条SELECT或WITH…SELECT；immutable/query_only/authorizer/进度及独立子进程限制拒绝写、DDL、PRAGMA、ATTACH、事务、多语句、扩展。函数仅处理已存SQL结果，限制10秒/256MB/64KB；次数见第2节。禁止import/属性访问/文件/网络/环境/shell/动态执行等，sorted key的单参数局部Lambda是明确有限例外，不开放任意Lambda。

首次重复失败提供一次纠正反馈，不重复执行；累计三次可恢复错误或纠正耗尽时预算内用现有事实/缺口收尾。授权/来源/协议致命错误仍终止。最终格式/引用/数值提交最多纠正一次，跨Attempt不清零。已知同范围季度缺口阻止等价空查，其他授权来源/补取路径保留。

问题澄清method在ask/chat共用层加载且无工具权限，冻结分析时钟/方法hash。对象、指标/口径、期间、交付目标有必要歧义才问；明确年份或已有确认直接执行。“近几年”等先列具体候选，补充继续同Turn；目标期间与可得期间分开，缺年不静默替换，自然年不冒充未知/不同财历的财年。

numeric_blocks只能指向本Turn成功SQL的verified_facts和真实回执；fact_id反查原快照身份，拒绝伪造值、错期间/原值同比、重复期和不兼容口径。Python用Decimal按期间绑定值/单位/来源，计算values/mean/range/population_stddev/changes并渲染；未知单位/倍率跨来源隔离，不补百分号/百分点。正文/claims不平行抄写数值趋势。未支持的公式仍需单独契约，不能宣称所有财务问答都已确定性验证。

InteractiveReport为临时结果，不改ResearchRun、评分或finish。只有明确memo-save或chat精确save（含/save/保存）才保存公司唯一主Memo；Agent无写工具。默认保存当前成功报告，等待/失败不能回退旧回答；显式turn-id可选已完成Turn。保存原文及全部引用、反方/缺口/下一步，不调用模型重写。重复同报告幂等，旧company_memos是不可变条目/history，主索引按ticker唯一；事务提交后导出data/stocks/<ticker>/memo.md，失败可重试修复。memo-list每公司一个入口，memo-read接受ticker/主ID/旧条目ID及--version；不自动合并跨上市代码。

## 11. 自然语言入口与进度

Router是一次受控分类组件，返回Pydantic RoutePlan，不能直接操作网络/数据库；Writer为受控写作组件，只有一个完整交互Agent。自然语言正式流程：识别公司/消歧 → 用户确认 → start/collect/write-draft/validate → 用户确认 → finish。

公司或意图歧义先列候选，不猜ticker；短名候选查询不直接作为研究证据。chat_sessions只管理自然语言确认/恢复，不能代替AgentTurn状态。精确save在Router前由Python处理。

TTY的chat --text作为首轮并继续等待确认/追问；管道/重定向一次性JSON。ProgressEvent只含phase/status/message/关联ID/时间等简短信息，到stderr；不包含隐藏推理、秘密、完整原文或工具结果。
