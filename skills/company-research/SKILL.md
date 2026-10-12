---
name: company-research
description: 用 wonder-stockresearch CLI 做单只港股/美股的公司研究（Q01–Q24、公司门闸、护城河评分）。当用户要求研究某只股票、生成公司研究报告、跑 run、补证据、复评或让 DeepSeek 写草稿时使用。触发词：研究 00728、中国电信、公司研究、moat、护城河、start/collect/fundamentals/save-draft/write-draft/validate/finish。
---

# 公司研究（wonder-stockresearch）

本 Skill 是项目内 Python `Skill Registry` 的研究方法上下文，不要求任何外部宿主。研究事实、来源归档、草稿、评分与最终报告均由本项目 Python CLI（`uv run wonder-research`）和 SQLite/`data/` 持有。你不能自己写数据库、改评分总分、伪造来源或直接生成最终报告；只能通过下面列出的项目 CLI/服务完成相应步骤，并对已归档的原文片段做分析与写作。

**职责划分（别越权）**：Router/Agent 只负责受控意图判断与问题理解；**调哪些富途接口由 Python 的 `collect` 固定**，不由模型选择；DeepSeek writer 只根据已归档来源写草稿并选择引用。凭据和数据连接均由项目运行时管理，不能由模型直接操作。

## 边界（必须遵守）

- 不请求、不读取、不打印账户、持仓、资金、订单或任何密钥；不做交易。
- 不伪造 `source_id`、`locator`、发布日期、`score_total`、质量状态或 run 状态。
- 不用价格/PE/PB 改变公司门闸或护城河分数；估值（Q25–Q28/G4）不在本流程内。
- 未通过 `validate` 不得 `finish`；不接受“结构合格就等于结论正确”。
- 来源必须**分段读取**：只用 `source-read` 的 `--page`/`--start-line`，禁止把整份 PDF 或全部历史塞进上下文。
- 研究正文用 UTF-8 JSON 文件传入（`--input file.json`），不要用 shell 字符串拼接正文。
- 如果用户的问题不是标准 Q01–Q24/门闸/护城河研究，而是风险、反方、宏观、同行、产能或其他临时问题，转交 `interactive-research-agent` Skill，使用 `ask`，不要把临时回答写入正式 ResearchRun。
- 用户明确要求“保存到公司备忘录”时，才使用 `memo-save`；Memo 也不能直接改变正式报告或评分。
- 如果用户使用口语或不严格表达，但意图明显是“写正式公司报告”，可以建议使用独立 `chat`；`chat` 会先确认，再执行固定流程到 `validate`，不会自动 `finish`。
- 如果用户只提供简称（例如“保利”），不要自行猜测 ticker；使用 `chat` 的候选消歧流程，等待用户选择完整名称、候选编号或股票代码。

## 命令入口

在本项目根执行（`--root` 可省略，默认当前目录）：

| 用途 | 命令 |
| --- | --- |
| 开始一次研究 | `uv run wonder-research start TICKER [--as-of ISO] [--baseline RUN]` |
| 采集真实资料 | `uv run wonder-research collect RUN [--kind all|market|fundamentals|filings]` |
| 列出来源 | `uv run wonder-research sources RUN` |
| 分段读取来源 | `uv run wonder-research source-read RUN SOURCE --page N` 或 `--start-line N [--limit-lines N]` |
| 手工补来源 | `uv run wonder-research source-add RUN --input source-request.json` |
| 保存草稿 | `uv run wonder-research save-draft RUN --input draft.json` |
| DeepSeek 写草稿 | `uv run wonder-research write-draft RUN --provider deepseek [--model M] [--temperature T] [--thinking enabled|disabled]` |
| 校验（不写完成） | `uv run wonder-research validate RUN` |
| 定稿 | `uv run wonder-research finish RUN` |
| 复评比较 | `uv run wonder-research compare RUN`（需 baseline） |
| 备份 | `uv run wonder-research backup --output data/backups/<名>` |
| 查看状态/历史 | `uv run wonder-research status RUN` / `history TICKER` |

所有命令输出 `{ok, data, issues}`；`exit 0` 正常，`2` 校验/身份拒绝，`1` I/O 或 provider 错误。

## 标准流程

1. **开始**
   ```bash
   uv run wonder-research start HK.00728
   ```
   记下 `data.run_id`；`as_of` 默认当前时间。后续所有来源必须“在 as_of 时点已可用”。

2. **采集**（`--kind all` 一次包含三类）
   ```bash
   uv run wonder-research collect RUN --kind all           # market + fundamentals + filings
   uv run wonder-research collect RUN --kind fundamentals  # 富途基本面变量（量化问题主力）
   uv run wonder-research collect RUN --kind filings       # HKEX/SEC 年报、中报/季报
   uv run wonder-research collect RUN --kind market        # 富途行情快照 + 财务原始数据
   ```
   查看 `run_steps` 与 `issues`：`blocked` 表示该通道当前不可用（如无 OpenD、SEC 缺 User-Agent），不是“没有资料”。采集不生成评分、不定稿。

   **调哪个富途接口由 Python 决定，不由你决定**：`collect --kind fundamentals` 固定调用 `futu_fundamentals.py` 里的数据集（公司概况、高管、财报、主营构成、分红回购、股东、板块、估值、分析师评级），并把每个数据集归档成一个 `provider` 来源，metadata 记 `dataset/evidence_class/available_from/currency/accounting_standard`。完整能力与只读边界见 [技术规格的网络与财务口径](../../docs/technical-spec.md#6-网络时间与财务口径)。你只需决定“要不要 collect”。

3. **盘点来源**
   ```bash
   uv run wonder-research sources RUN
   ```
   拿到每个来源的 `source_id`、`kind`、`title`、`published_at`、`extraction_status`、`text_path`。
   - `kind=primary` 且**有已知发布日期**的来源才能支撑核心结论；发布日期未知的 primary 只能作 `context`。
   - `kind=provider` 分两类：有 `metadata.available_from`（报告期末/披露日，如财报/分红/主营/股东/高管）且不晚于 as_of 的**已报告数据可支撑核心结论**；`evidence_class=snapshot` 的实时快照（行情/估值/评级/板块）与晚于 as_of 的数据只能作 `context`。
   - `secondary` 只作背景，不独立支撑核心结论。

4. **分段读原文并做笔记**
   ```bash
   uv run wonder-research source-read RUN SOURCE --page 12
   uv run wonder-research source-read RUN SOURCE --start-line 200 --limit-lines 120
   ```
   每次只读一段，回应里说明读了哪一页/哪几行；引用时 `locator` 必须与真实返回一致（PDF 用 `page N`，文本用 `lines a-b`）。

5. **生成草稿**（两条路径，任选）
   - 路径 A（人工/外部调用方提供草稿）：按下面的形状写 `draft.json`，再由项目 CLI `save-draft`。
   - 路径 B（DeepSeek 写）：`uv run wonder-research write-draft RUN --provider deepseek`。它从**已归档来源**一次性生成草稿并自动执行 `save-draft`+校验；需要运行环境有 `DEEPSEEK_API_KEY`（可放项目根 `.env`）。它**不会自动 `finish`**；缺 key 报 `missing_api_key`，模型输出不合格报 `invalid_json`/`invalid_schema`（不生成完成报告）。默认 `thinking=disabled`、`temperature=0.2`。

   草稿形状沿用阶段 1：`{"draft": ResearchDraft, "sources": []}`。因为采集/手工导入的来源已经挂在 run 上，`sources` 留空即可；证据 `source_id` 用第 3 步拿到的真实 id，且 answered/pass-fail/非 null 评分只能用“可作 supports”的来源。每项：
   - `answers`：Q01–Q24，每项 `answered|partial|missing`；`partial/missing` 必须给 `gap`（缺什么、去哪找、下次核验、影响）。
   - `gates`：G1/G2/G3/G5/G6/G7，`pass|fail|insufficient`。
   - `moats`：M1–M6，`score` 0–10 或 `null`，附 `confidence`、理由、支持/反方证据。
   - `challenge`：最强反方、检索渠道与范围、是否找到反方、可能推翻结论的证据、下次核验。
   - `conclusion`；`review_explanations` 无 baseline 时留空。
   - **总分不要自己写**：`score_total`/`quality_status` 由程序派生；draft 中不得出现 `valuation`/`target_price`/`action`。
   - 事实用 `fact` 且必须带引用；数值用结构化 `numbers`（value/unit/currency?/period/basis/evidence）。

6. **保存与迭代**
   ```bash
   uv run wonder-research save-draft RUN --input draft.json
   uv run wonder-research validate RUN
   ```
   `save-draft` 允许内容不完整并返回 issues；以 `validate` 为准。常见拒绝：
   - `missing_id`/`duplicate_id`：覆盖或重复。
   - `unknown_source`：引用了未归档的 source_id。
   - `ineligible_as_of_support`：用无发布日期的 primary、晚于 as_of 的 primary、无 `available_from` 的 provider 快照（估值/行情/评级），或 `available_from` 晚于 as_of 的 provider 数据支撑核心结论 → 改引已报告数据/一手披露，或把该项降为 `partial/missing`、门闸 `insufficient`、评分 `null`。
   - `missing_authoritative_support`：answered/pass-fail/非 null 评分缺 primary/provider 支持。
   - `unresolved_critical_conflict`：关键冲突未解决。
   证据不足就诚实降级为 `partial`/`missing`/`insufficient`/`null`，不要编造。

7. **定稿**
   ```bash
   uv run wonder-research finish RUN
   ```
   仅当 `validate` 无 error 时成功，生成 `data/stocks/<ticker>/runs/<时间>-<run_id>/{company.md,research.json}`。完成后不可改。

## 更换会话后继续

不依赖聊天记忆。用 `status RUN` 看状态与 `run_steps`，用 `sources RUN` + `source-read` 重新读取已归档片段，必要时重新 `save-draft`（未完成时）。`completed` 的 run 不能覆盖。

## 反模式（禁止）

- 只读摘要就宣称“已核对原文”；把整本 PDF 灌进上下文。
- 编造来源、页码、发布日期、数字或评分；用价格变动解释公司分数变化。
- 绕过 `validate` 直接 `finish`，或手工改 `data/` 下的报告/数据库。
- 把 provider 的实时快照（无 `available_from` 的行情/估值/评级）当作 as_of 时点的历史证据；把 `available_from` 晚于 as_of 的 provider 数据当证据。
- 越过 `collect` 自己选择富途接口，或让 writer 自动 `finish`。
