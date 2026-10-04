# 阶段 4：真实资料与富途只读接入

## 1. 本阶段解决什么问题

前三阶段的数据都由用户手动提供。这一阶段让程序自己取得两类真实资料：

1. **行情与财务**（富途 OpenD，只读）——`market` 只取得个股快照；`fundamentals` 是唯一的财务报表入口，分页归档每类最多 20 个去重年度期的利润表、资产负债表、现金流量表和关键指标；主营构成尽可能归档最多 10 个年报期和最近 3 个中报期。
2. **公告与定期报告**（港交所 HKEXnews / 美国 SEC EDGAR 公开渠道）——默认回看 5 年，归档最近 5 份年报和最近 3 份中期/季度定期报告。

同时加入一张轻量运行步骤账本 `run_steps`，给每个采集子任务记录 attempt、状态、输入哈希、输出来源、provider 和错误。它是 `runs` 的执行明细，不是事件溯源或通用任务队列。

阶段 3 的 `source-add`/归档/哈希/提取被复用：采集到的每个快照和 PDF 都走同一套字节归档。

## 2. 数据流与调用链

```text
collect RUN --kind market|filings|all
    │
    ▼
research.collect()
    ├── 每个子任务先写 run_steps(status=running)，然后释放锁
    ├── 网络调用（不持项目写锁）
    │     market:    market.FutuQuoteProvider → OpenD OpenQuoteContext（只读快照）
    │     fundamentals: futu_fundamentals.FutuFundamentalsProvider → OpenD（财务与基本面）
    │     filings:   disclosures.Hkex/SecProvider → 官方检索接口
    │                → 选中后经 sources.fetch_url 下载
    ├── 重新取锁，登记来源（provider / primary / secondary）并写 run_steps
    └── 失败/受限写 failed/blocked + issues，绝不当作“没有资料”
```

## 3. 实际文件与入口

- `src/wonder_stockresearch/storage.py`：`SCHEMA_VERSION = 2`，新增 `run_steps` 表与 `_MIGRATIONS`（v1→v2 显式迁移），以及 `next_step_attempt`/`insert_step`/`finish_step`/`list_steps`。
- `src/wonder_stockresearch/market.py`：`FutuQuoteProvider`，惰性 import `futu`，`SysConfig.enable_console_log(False)` 防止 SDK 日志污染 stdout，先做 2 秒 TCP 端口探测再连接，只调用 `OpenQuoteContext` 的 `get_market_snapshot`，**不导入任何账户/交易接口**。财务报表由 `futu_fundamentals.py` 的单一路径分页请求。
- `src/wonder_stockresearch/disclosures.py`：`HkexDisclosureProvider`（`prefix.do` 查 stockId，`titleSearchServlet.do` 取公告并保留最多 200 条相关候选）、`SecDisclosureProvider`（EDGAR submissions，需 `WONDER_SEC_USER_AGENT`）、`classify_title`/`select_filings`。当前默认回看 5 年并选取最近 5 份年报 + 3 份中期/季度报告。
- `src/wonder_stockresearch/research.py`：抽出 `_register_source`（归档+登记，`source_add` 与 `collect` 共用）；新增 `_begin_step`/`_end_step`、`_collect_market`、`_collect_filings`、`collect`；`get_status` 增加 `steps`。
- `src/wonder_stockresearch/cli.py`：新增 `collect RUN_ID [--kind all|market|filings]`。
- `pyproject.toml`：新增 `futu-api==10.11.7108`。
- `tests/test_run_steps.py`、`tests/test_collect.py`（新）。

`run_steps`：

```sql
run_steps(run_id FK, step_key, attempt, status, input_hash, output_ref,
          provider, model, started_at, finished_at, error_json,
          PRIMARY KEY (run_id, step_key, attempt))
```

`status` 只允许 `pending/running/succeeded/failed/blocked/skipped`；`output_ref` 形如 `source:<uuid>`，只指向已有来源，不放原文。

## 4. 最小可运行示例与真实结果

```bash
uv run wonder-research --root "$DEMO" start HK.00728       # 记下 run_id
uv run wonder-research --root "$DEMO" collect "$RUN" --kind filings
uv run wonder-research --root "$DEMO" collect "$RUN" --kind market
uv run wonder-research --root "$DEMO" status "$RUN"
```

阶段 4 实现后实跑（本机有 OpenD，网络可用）：

```text
filings → ok=true，candidate_count=18，selected_count=4，downloaded=4
          归档：2026 INTERIM REPORT、ANNUAL REPORT 2025/2024/2023 + 公告索引
market  → ok=true，market_snapshot succeeded
          stdout 为纯 JSON（futu 日志已关闭）
fundamentals → 财务报表经单一路径分页归档；每类最多 20 个去重年度期，保留原始期间、币种和会计准则
snapshot→ 中国电信 00728，last_price 4.435
source-read --page 2 → locator="page 2"，total_pages=251
```

## 5. 为什么这样实现

- **领域核心仍是函数**：采集只是把外部数据变成 `sources` 与 `run_steps`；评分、校验、`finish` 完全不变，模型或未来的 Agent 无法借此改数据库或总分。
- **只读、不碰账户**：仅使用行情上下文；不调用任何账户、持仓、资金、订单或交易接口；用户独立启动并登录本机 OpenD，本项目仅连接行情端口，不读取或保存账户秘密。
- **时间两把尺**：`retrieved_at` 是取得时间，`observed_at` 是数据观测时间，二者都写进 provider 来源；若 `observed_at` 晚于 run 的 `as_of`，metadata 记 `observed_after_as_of=true`，只作本次下载结果，不悄悄前移研究截止时点。
- **保留原始口径，不猜单位**：财务来源原样保存 `period_text/date_time_str/fiscal_year/financial_type/currency_code/accounting_standards` 和 `item_list(field_id/display_name/data)`；futu 未给 item 级单位，程序就标注 `unit_note`，不推断单位，也不按列表下标拼财年。
- **失败可解释**：网络/接口错误写 `run_steps.error_json` 并入 `issues`；`opend_unavailable`/`sec_user_agent_missing` 记为 `blocked`（warning），真实错误记为 `failed`（error）。`collect` 只在至少一个子任务成功时退出 0。
- **不持锁下载**：网络在锁外完成，登记时才取项目写锁；与阶段 3 一致。
- **官方渠道**：HKEXnews 与 SEC EDGAR；SEC 要求带联系方式的 User-Agent，因属用户运行环境配置，未设置时该通道标 `blocked`，不影响 HKEX 与其他能力。

## 6. 测试覆盖与不能证明的事

`tests/test_run_steps.py`：v1 数据库经 `initialize` 迁移到 v2、旧数据保留、attempt 递增、步骤状态进入 `status`。

`tests/test_collect.py`：行情/财务来源归档与步骤记录、财务期间/币种/会计口径/字段/endpoint 保留、provider 超时记为 `failed` 且不伪装成空成功、OpenD 不可用记为 `blocked`、公告选取最近 5 年报 + 3 份中期/季度报告、部分下载失败与全部失败、空结果单独记录、SEC 缺 User-Agent、市场路由、CLI `collect` 接线。

这些测试**不能**证明：Provider 数据一定完整或正确、公告发现覆盖所有文件、SEC 通道在当前环境可用、公司判断正确或证券值得投资。Fixture 测试全部离线，唯一的真实请求是实施时记录的 smoke（HKEX 下载、Futu 快照/财务、`source-read` 翻页）。

## 7. 小练习

1. `collect "$RUN" --kind market`，然后 `status "$RUN"` 查看 `steps` 中两个子任务的 `started_at/finished_at`、`input_hash`、`output_ref`。再次 collect：`attempt` 递增到 2，形成可重试痕迹。
2. 把 OpenD 关掉再 `collect --kind market`：预期快速返回 `blocked`（TCP 探测 2 秒内失败），退出码 1，`issues` 为 warning。
3. 删掉某个已归档 PDF，再 `validate "$RUN"`：预期 `missing_archived_file`（阶段 3 的归档校验）。

## 8. 已知限制与下一阶段

- 富途三大表与关键指标取 ANNUAL 口径、每类最多 10 期；主营构成按接口历史期保存最多 10 个年度期与最近 3 个中报期。覆盖以实际 `period_text/date_time_str` 为准，不能只以请求条数推断连续年数；关键指标币种可能与报表不同（实测为 HKD）；item 级单位 futu 未提供，保留原值不推断。
- 公告发现按英文标题分类，中文/双语标题与“业绩公告”等更细分类仍在候选层，未做全文解析。
- SEC 通道需 `WONDER_SEC_USER_AGENT`，本环境未配置，标 `blocked`；未对真实美股做端到端 smoke。
- 不做 OCR、不执行网页脚本；不做模型写作、估值或附记正文。项目不依赖 OpenCode 或外部宿主。
