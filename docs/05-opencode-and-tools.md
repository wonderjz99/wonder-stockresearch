# 阶段 5：OpenCode + CLI 端到端研究

## 1. 本阶段解决什么问题

前四阶段已经有了可用的 Python CLI。这一阶段把 OpenCode 接上：用户在 OpenCode 会话里说“研究 HK.00728”，宿主按 project skill 的步骤调用 CLI，采集资料、分段读原文、写草稿、校验、定稿。程序负责事实与状态，OpenCode 只负责操作与写作；会话中断或更换后，研究可由 `run_id` + SQLite + `data/` 重建。

本阶段没有新增 Python 业务模块，交付物是 **skill、宿主配置、一次真实端到端研究**，以及为宿主可用性做的两处小修（见第 3 节）。

## 2. 数据流与职责

```text
用户
  │  自然语言
  ▼
OpenCode（宿主/操作员）
  │  按 skills/company-research/SKILL.md 调用受限 CLI
  ▼
uv run wonder-research start/collect/sources/source-read/save-draft/validate/finish
  │
  ▼
research.py + storage.py（事实来源：SQLite、sources、草稿、评分、finish）
  │
  ▼
data/stocks/HK.00728/{sources/*, runs/<时间>-<run_id>/{company.md,research.json}}
```

- OpenCode **不能**直接写数据库或报告，不能改 `score_total`，不能伪造 `source_id`/`locator`/日期/完成状态。
- 来源阅读必须分段：`source-read --page N` 或 `--start-line N`，不把整本 PDF 放进上下文。

## 3. 实际文件与入口

- `skills/company-research/SKILL.md`（新）：唯一正文。frontmatter `name: company-research`，描述含触发词；正文写清允许的命令、标准流程、as_of 可用性规则、反模式。
- `opencode.json`（新）：注册非默认目录
  ```json
  { "$schema": "https://opencode.ai/config.json", "skills": { "paths": ["skills"] } }
  ```
  OpenCode 递归扫描 `skills/**/SKILL.md`，因此 `skills/company-research/SKILL.md` 被发现；不另建 `.opencode/skills/` 副本，保持唯一正文。**修改该文件后需重启 OpenCode 才会加载。**
- 为宿主可用性的两处小修（`research.py`）：
  1. **核心证据 as_of 可用性**：`_is_core_eligible` + `_core_support_issue`。发布日期未知的 primary 只作 `context`；`published_at` 晚于 `draft.as_of` 的来源、以及 `metadata.observed_after_as_of=true` 的 provider 不能支撑核心结论。该规则覆盖 answered 问题、pass/fail 门闸、非 null 护城河评分；有不可用权威引用时报 `ineligible_as_of_support`，完全没有时报 `missing_authoritative_support`。采集到的资料本身照常归档、不删除。
  2. **save-draft 合并已挂来源**：`_effective_sources`。采集/手工导入的来源已挂在 run 上，草稿里 `sources: []` 即可，校验时把它们与 packet 内来源合并，避免误报 `unknown_source`；真正未归档的引用仍会报错。

## 4. 真实端到端研究（HK.00728）

全部命令都在项目根执行，`--root` 省略即当前目录。

```bash
uv run wonder-research start HK.00728                       # run 2d441bba-…
uv run wonder-research collect 2d441bba-… --kind filings     # HKEX 3 年报 + 1 中报
uv run wonder-research collect 2d441bba-… --kind market      # 富途快照 + 财务（背景）
uv run wonder-research sources 2d441bba-…                    # 取得 source_id
uv run wonder-research source-read 2d441bba-… 318520f5-… --start-line 816 --limit-lines 20
uv run wonder-research save-draft 2d441bba-… --input draft.json
uv run wonder-research validate 2d441bba-…                   # exit 0
uv run wonder-research finish 2d441bba-…                     # 生成正式报告
```

实际结果：

```text
collect filings → succeeded，candidate_count=18，selected=4（ANNUAL 2025/2024/2023 + 2026 INTERIM）
collect market  → succeeded，快照 + 4 类报表；provider observed_after_as_of=true，仅作 context
sources         → 7 条：4 primary（已知发布日期，可用）、2 provider（context）、1 secondary 索引
save-draft      → ok=true，source_count=7，issues=[]（sources=[] 但合并了已挂来源）
validate        → exit 0，issues=[]
finish          → completed，quality_status=evidence_incomplete，score_total=null
                  data/stocks/HK.00728/runs/20260920T162000Z-2d441bba-…/{company.md,research.json}
```

草稿内容来自真实分段读取，例如：
- 经营收入 529,559 百万元、服务收入 485,424 百万元、净利润 33,185 百万元、EPS 0.36、EBITDA 143,872 百万元、资本开支 80,364 百万元、自由现金流 447 亿元（引 `lines 800-820`、`lines 2243-2255`、`lines 2464-2495`）。
- 主营数字信息服务（`lines 2903-2910`）。
- 五大客户收入 <30%、总负债 335,668 / 总权益 431,834、短期债务 8,801、净流动负债 154,323（引 `lines 3480-3495`、`lines 13430-13446`）。

24 问中只有 Q01/Q03/Q11/Q23 标 `answered`，其余为 `partial`/`missing` 并附缺口；门闸仅 G1=pass，其余 insufficient；护城河仅 M5=7，其余 null，总分因此为未知（evidence_incomplete）。这是有意保守：资料不足就降级，不编造。

**更换会话后继续**（新进程只给 `run_id`）：

```text
status 2d441bba-…      → completed，answers=24，sources=7，steps 三项 succeeded
sources 2d441bba-…     → 仍可列出全部来源
source-read … --start-line 816 → locator lines 816-820，真实原文
save-draft（重试）      → ok=false，issues=['run_completed']，不覆盖
```

非法草稿会被拒绝：把某个 `source_id` 改成不存在的 UUID 后 `validate-packet` 退出码 2，报 `unknown_source` 与 `missing_authoritative_support`。

## 5. 为什么这样实现

- **宿主不是事实来源**：研究上下文全部落在 SQLite 与 `data/`；OpenCode 会话只是操作过程，断了也能从 `run_id` 重建。
- **写作与校验分离**：宿主写 JSON 草稿，Python 校验结构、引用、时间、评分；`score_total`/`quality_status` 由程序派生，模型不能填。
- **时间可用性进入校验**：仅“引用了某个来源”不够，来源必须在 `as_of` 时点可用。未知日期的一手来源、晚于 as_of 的材料、观测晚于 as_of 的 provider 快照都不能独立支撑核心结论。
- **唯一正文**：skill 只在 `skills/company-research/SKILL.md` 维护，通过 `skills.paths` 注册，避免两份副本漂移。
- **受限工具面**：skill 只列出现有 CLI 命令，不提供任意 Python/SQL 执行，也不接 MCP 或模型 SDK。

## 6. 测试覆盖与不能证明的事

- 自动化测试（`tests/`，含新增 `tests/test_evidence_as_of.py`）覆盖 as_of 可用性规则、save-draft 合并、非法引用拒绝、finish 门禁等；`uv run pytest` 与 `uv run ruff check .` 全绿。
- skill 本身**没有**自动化测试（它是 OpenCode 配置，不是 Python 代码）；第 4 节的 HK.00728 是人工监督的真实 smoke，非 CI。
- 不能证明：模型会始终遵守 skill、Provider 数据完整正确、缺口判断准确、公司结论正确或证券值得投资。

## 7. 小练习

1. 按 skill 复跑一次 `source-read` 的不同页/行，确认 `locator` 与返回一致；把某个 `answered` 问题的引用换成 provider 快照（`observed_after_as_of=true`），预期 `validate` 报 `ineligible_as_of_support`。
2. 在 `skills/company-research/SKILL.md` 中把“sources 留空”改成显式示例；重启 OpenCode 后确认 skill 仍被唯一加载。
3. 用同一 `run_id` 在另一个终端执行 `status` 与 `sources`，验证不依赖聊天历史。

## 8. 已知限制与下一阶段

- 需要重启 OpenCode 才能发现新 skill；本机是否启用取决于用户配置。
- 一次性研究仍有大量 `partial`/`missing`：历史多年资料需要宿主逐份补入，程序不自动补齐十年。
- 未实现估值、附记正文、compare/backup；未接模型 API、MCP、LangGraph。
- 下一阶段（阶段 6）：`start --baseline`、`compare`、`backup` 与 `review.md` 复评比较。
