# 富途（futu-api）数据能力清单

本文件总结本机 `futu-api==10.11.7108` 的 `OpenQuoteContext`（只读行情上下文）能为公司研究提供什么，以及哪些不能用。阶段 13 已于 2026-09-26 实测归档中国移动 `HK.00941`、中国联通 `HK.00762`、中国电信 `HK.00728` 的历史报告与基本面数据；逐家覆盖见下方“阶段 13 实测语料”。

**只读边界**：只调用 `OpenQuoteContext`。不使用 `OpenSecTradeContext`，不读取账户、持仓、资金、订单，不调用自选分组/到价提醒等用户数据接口。`futu` 惰性 import，离线导入本项目不连接服务。

## 1. 接入方式

- 需要本机运行 OpenD（默认 `127.0.0.1:11111`，可用 `WONDER_FUTU_HOST`/`WONDER_FUTU_PORT` 覆盖）。
- 代码：`src/wonder_stockresearch/market.py`（行情快照）与 `src/wonder_stockresearch/futu_fundamentals.py`（财务与基本面变量）。
- CLI：`collect RUN --kind market`（行情快照）、`collect RUN --kind fundamentals`（财务和基本面）、`collect RUN --kind all`（包含两者）。
- 失败按 `run_steps` 的 `failed`/`blocked` 记录，其他来源继续。

## 2. 基本面接口（实测可用，HK.00728）

| 接口 | 提供的数据 | 港/美 | 有期间/披露日 | as_of 可用性 | 主要帮助的问题 |
| --- | --- | --- | --- | --- | --- |
| `get_company_profile` | 公司业务/简介、行业、上市日、员工数、审计机构、注册地、年结日、IR | 港/美 | 无（稳定事实） | 快照规则 | Q01、Q16、Q22 |
| `get_company_executives` | 高管姓名/职务/任职起始/披露日/年龄/学历/年薪 | 港/美 | 有（`issue_date_str`） | 可支撑 | Q13、Q14、Q16 |
| `get_company_executive_background` | 高管背景 | 港/美 | — | 快照 | Q13、Q14 |
| `get_financials_statements` (1–4) | 利润表/资产负债表/现金流量表/关键指标，含期间/币种/会计准则/字段；以 `next_key` 分页、每类保留最多 20 个去重年度期 | 港/美 | 有（`date_time_str`） | 可支撑 | Q03、Q07、Q09、Q10、Q11、Q12、Q14、Q23、Q24 |
| `get_company_operational_efficiency` | 人均收入/人均利润/员工数及同比、币种 | 港/美 | 有（`end_date_str`） | 可支撑 | Q03、Q07、Q10 |
| `get_financials_revenue_breakdown` | 按产品/地区/业务的收入与占比；归档接口报告的历史日期列表，并查询最多 10 个年报期和最近 3 个中报期 | 港/美 | 有（`period`/`screen_date_list`） | 可支撑 | Q01、Q02、Q23 |
| `get_corporate_actions_dividends` | 分红历史：`pub_date`/记录日/除息日/派息日/方案 | 港/美 | 有（`pub_date`） | 可支撑 | Q11、Q14 |
| `get_corporate_actions_buybacks` | 回购（HK/A 分开） | 港/美 | 有（公告日） | 可支撑 | Q11 |
| `get_shareholders_overview` | 主要股东与持股类型、`static_date_str`、持股比例 | 港/美 | 有（`static_date_str`） | 可支撑 | Q13、Q23 |
| `get_shareholders_institutional` / `holder_detail` | 机构数量与持股变动（按期） | 港/美 | 有（期间） | 可支撑 | Q13 |
| `get_owner_plate` | 所属行业/概念板块 | 港/美 | 无 | 快照 | Q01、Q02、Q04、Q22 |
| `get_valuation_detail` | PE 走势/分位、行业分布、利润增速 | 港/美 | 无（实时） | 仅 context；标准 `collect` 不调用 | 未来估值 Skill |
| `get_research_analyst_consensus` | 分析师评级与目标价区间 | 港/美 | 无（实时） | 仅 context；标准 `collect` 不调用 | 未来估值 Skill |
| `get_earnings_calendar` / `get_dividend_calendar` | 财报/派息时间表 | 港/美 | 有日期 | 可支撑（时间线索） | 复评触发、Q11 |

## 3. 行情/资金类（只作背景 context）

`get_market_snapshot`（价格/市值/PE/PB/股本等）、`get_capital_flow`、`get_capital_distribution`、`get_stock_quote`、K 线、`get_rise_fall_distribution`、`get_top_ten_buy_sell_brokers`、`get_short_interest`/`get_daily_short_volume`、`get_rating_change`、宏观指标等。这些是时点快照，不能支撑公司判断。

## 4. 明确不能用（边界）

- `OpenSecTradeContext` 及所有账户/交易接口：账户、持仓、资金、订单、下单/改单/撤单。
- `get_user_security` / `get_user_security_group` / `modify_user_security`：自选股分组（禁止修改）。
- `set_price_reminder` / `get_price_reminder`：到价提醒（用户设置）。
- 仅美股的接口（如 `get_insider_holder_list`、`get_holding_change_list`）对港股返回“不支持”，本版不依赖。
- 期权/期货/窝轮/事件合约等非公司研究范围。

## 5. as_of 可用性与归档

`futu_fundamentals.py` 把每个数据集规范化成统一结构，`collect` 归档为 `provider` 来源并在 metadata 记录：

- `dataset`：数据集名（profile/statements/dividends/…）。
- `evidence_class`：`reported`（有可确定的期间/披露日）或 `snapshot`（实时）。
- `available_from`：报告期末/披露日（如 `2025-12-31`、`2026-08-20`）；无则 null。
- `currency`、`accounting_standard`、`observed_at`、`observed_after_as_of`。
- `endpoint`：`futu.<dataset>`；原始 JSON 完整归档。

校验规则（研究规格第 6 节“方案 4”）：**provider 有 `available_from` 且不晚于 as_of 时可支撑核心结论；没有 `available_from` 的实时快照只能作 context**。因此财报/分红/主营/股东/高管可当证据，估值/评级/行情仅背景。

## 6. 已知限制

- 依赖本机 OpenD 与相应行情权限；F10 类接口可能有额度/频率限制，失败时记 blocked，不影响其他来源。
- 财务数据缺 item 级单位，保留原始值与币种，不推断单位。
- 实际覆盖必须读取归档中的 `period_text`/`date_time_str`，10 条并不自动表示连续 10 个财年；主营构成受 Futu 对该证券提供的 `screen_date_list` 限制，单个历史期拉取失败会留在 `history_failures`，不能伪称完整。
- 三家电信公司数据语料基线：FY2021–FY2025 年报、2024H1/2025H1/2026H1 中期报告、2016–2025 十个年度结构化财务期，以及最多 10 个可用年报期和 3 个中报期的主营构成。新 run 可按接口分页将每类财务扩展到最多 20 个去重年度期；主营构成的历史年份受 Futu 的实际期间清单约束，可能不连续；精确期间记在 `PROGRESS.md`。
- 港股无内部人持股/大股东变动接口（美股才有）。
- 部分接口返回空（如无回购），需按“空结果”如实记录，不当作“没有”。

## 7. 实测结果（HK.00728，2026-09-21）

这是阶段 13 的历史实测，当时的 `collect --kind all` 有 8 个子任务：`market_snapshot`、`financial_statements`、`fundamentals_company`、`fundamentals_financials`、`fundamentals_revenue`、`fundamentals_capital`、`fundamentals_valuation`、`filings`；共归档 4 份 PDF 公告 + 11 个基本面数据集 + 2 个行情/财务来源。阶段 18 起，`financial_statements` 已合并到唯一的 `fundamentals_financials`，标准采集不再请求 `fundamentals_valuation` 或 consensus。

对比 writer 效果（同一份 HK.00728 草稿）：

| | 仅有年报 filings | 加入 fundamentals |
| --- | --- | --- |
| answered | 1 | 24 |
| partial | 23 | 0 |
| 门闸 | 6 insufficient | 6 pass |
| 护城河 | 全 null | M1–M6 均有分 |

结论：量化与结构类问题基本可由富途数据覆盖；剩余缺口主要是定性问题（技术替代、份额趋势、提价能力、品牌、认错纠错、客户价值/渠道/留存/必需性、破坏情景）与引用纪律，仍由年报/公告片段与人工补源处理。

## 8. 阶段 13 实测语料（2026-09-26）

每家公司均成功归档 8 份 HKEX 定期报告（FY2021–FY2025 五份年报 + 2024H1、2025H1、2026H1 三份中期报告），PDF 文本提取均为 `available`。每个干净验证 run 另有公告索引和 11 个基本面 provider 数据集；利润表、资产负债表、现金流量表、关键指标各含 2016/FY–2025/FY 十个连续年度期，币种 CNY。中国移动和中国电信的结构化财务口径为国际会计准则；中国联通为香港会计准则。原始期间、币种和口径仍以逐条 provider 源为准。

主营构成均归档 10 个接口可用年报期与 2024H1、2025H1、2026H1，当前干净来源的 `history_failures` 均为空。年报期间在不同公司间并不完全一致：移动覆盖 2015、2016、2018–2025；联通覆盖 2016–2025；电信覆盖 2014–2016、2019–2025。缺年表示该证券接口可用历史期间不连续，不应补值或声称连续十年。

完整 run ID、逐家公司来源列表、运行结果与一项标题误分类修复记录见 [`PROGRESS.md`](../PROGRESS.md)。报告数据写入项目 `data/`，由 `.gitignore` 排除；本阶段未生成正式研究报告或运行 DeepSeek 写作质量测试。
