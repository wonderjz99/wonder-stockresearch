# 项目进度

## 当前状态

- 当前阶段：2，SQLite、草稿与股票历史已实现；按教学约定在此暂停。
- 业务软件：最小 Python 包、离线 CLI、结构校验、临时渲染，以及持久化研究记录（watch/start/save-draft/validate/finish/resume/status/history）。
- 真实数据接入、来源字节归档、OpenCode skill、附记正文、估值与复评 compare：仍未实现。

## 已准备的交接文件

README.md、AGENTS.md、PLAN.md、docs/research-spec.md、docs/technical-spec.md、docs/teaching-contract.md、docs/provenance.md 和本文件。

## 下一步（用户确认后继续）

实施 PLAN 的阶段 3：来源存档与证据定位。实现 source-add、sources、source-read，获取原始字节并计算 SHA-256、提取文字型 PDF/网页文本、返回 page/行号 locator。先核对阶段 2 代码和测试；不要提前实现富途、OpenCode skill、附记正文或估值。

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

## 补充交接：后续 LLM API 写作

用户补充希望首版后能够直接接 DeepSeek 等模型 API 写报告。已同步 AGENTS.md、README.md、PLAN.md 和技术规格第 8 节。保持首版 CLI/OpenCode 方案与阶段顺序；后续 API 写作复用 ResearchDraft、证据、校验和渲染服务，不依赖旧会话，不提前开发 Agent 循环。当前没有安装 SDK、调用模型或读取凭据。
