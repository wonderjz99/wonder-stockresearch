# 项目进度

## 当前状态

- 当前阶段：6，手动复评、备份与维护已实现；按教学约定在此暂停。
- 业务软件：最小 Python 包、离线 CLI、结构校验、临时渲染、持久化研究记录、来源导入/归档/片段读取、collect（富途只读行情/财务 + HKEX/SEC 公告发现）与 run_steps 账本、project skill `skills/company-research/`（`opencode.json` 注册），以及复评 `compare`、`backup` 与有 baseline 时 `finish` 生成 `review.md`。已完成 HK.00728 首次研究及一次复评与备份/恢复 smoke。
- 附记正文、估值，以及阶段 7 的 DeepSeek 直接模型写作：仍未实现（后者为本版本必做）。

## 已准备的交接文件

README.md、AGENTS.md、PLAN.md、docs/research-spec.md、docs/technical-spec.md、docs/teaching-contract.md、docs/provenance.md 和本文件。

## 下一步（用户确认后继续）

实施 PLAN 的阶段 7（本版本必做）：新增小型 Python writer，从指定 run 的已归档来源构造受限上下文，调用 DeepSeek 生成既有 `ResearchDraft`；新增 `write-draft RUN_ID --provider deepseek [--model MODEL]`，只保存草稿并调用既有 `validate`，不自动 `finish`。凭据只从环境变量 `DEEPSEEK_API_KEY` 读取，不写入项目/日志/数据库/对话。先核对阶段 6 代码与测试；不要提前实现估值、附记正文、MCP 或 LangGraph。真实 API smoke 在用户准备好环境后单独进行。

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
