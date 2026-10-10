# 阶段 6：手动复评、备份与维护

## 1. 本阶段解决什么问题

阶段 5 完成了首次研究。这一阶段支持在旧研究之上发起**手动复评**：`start --baseline` 关联上一次完成的研究，`compare` 用 Python 计算问题状态、门闸、评分与证据的结构差异，`finish` 把差异固化成 `review.md`。旧报告与旧原文不改写。

同时提供 `backup`：用 SQLite backup API 创建一致性数据库快照，连同已归档来源与完成报告复制到 `data/backups/` 下的新目录，并写 manifest 与恢复说明，便于恢复到临时根目录验证。

## 2. 数据流与调用链

```text
start TICKER --baseline BASELINE_RUN
    │  校验：同 ticker、baseline 已完成、baseline.as_of ≤ 新 run
    ▼
save-draft → validate → compare（可对草稿预览）
    │  Python 逐项算差异；缺分值差值 = 不适用
    ▼
finish
    │  校验通过后写 company.md、research.json（含 comparison）、review.md
    ▼
backup --output data/backups/<name>
    │  项目写锁 → SQLite backup → 拷贝 sources/ 与 completed run 文件 → manifest.json + RESTORE.md
    ▼
data/backups/<name>/{wonder.db, files/…, manifest.json, RESTORE.md}
```

## 3. 实际文件与入口

- `src/wonder_stockresearch/fixed/research.py`
  - `compare_runs(root, run_id)`：要求 `baseline_run_id`；草稿可预览，返回结构化差异。
  - `_build_comparison(connection, run, baseline)`：纯计算。逐项比较 Q01–Q24 状态、G1/G2/G3/G5/G6/G7 结果、M1–M6 分数与总分；`_score_diff` 保证任一侧为 `null` 时差值为 `null`（不适用）；`comparable=false` 时不算分差。
  - `_draft_referenced_source_ids(draft)` + run_sources 集合差：区分**本次新增来源**与**沿用基准来源**，旧来源保留原始 `retrieved_at`/`published_at`，不能冒充新取得。
  - `backup(root, output, *, lock_timeout=...)`：在项目写锁内执行；`_backup_destination` 限定目标在 `root/data/backups/` 下且不存在。
  - `finish_run`：有 baseline 时重新校验 baseline 已完成，计算 comparison，写 `review.md`，并把其哈希纳入 `artifact_hashes_json`。
- `src/wonder_stockresearch/storage.py`：`backup_database`（`sqlite3` backup API）、`list_all_sources`、`list_completed_runs`。
- `src/wonder_stockresearch/fixed/reports.py`：`render_review(comparison)` 固定结构 Markdown，缺分差显示“不适用”。
- `src/wonder_stockresearch/cli.py`：新增 `compare RUN_ID` 与 `backup --output PATH`；并捕获 `storage.StorageError`（锁超时等）为 exit 1，而非抛栈。
- `src/wonder_stockresearch/fixed/research.py`（核心证据 as_of 可用性收口）：`_is_core_eligible` 现在对 provider 直接读取 `metadata.observed_at` 与 `draft.as_of` 比较，并仍要求 `observed_after_as_of` 不为 true；只有日期（`publication_precision=date`）的 primary 以当地日终为保守可用时点。两者都只在 as_of 时点已可用时才能支撑 answered/pass-fail/非 null 评分。
- `tests/test_review_and_backup.py`：6 个离线测试；`tests/test_evidence_as_of.py`：补充 provider `observed_at`、缺 `observed_at` 与日期精度用例。

## 4. 最小可运行示例与真实结果

在一份已有 HK.00728 完成研究 `BASELINE` 的项目根上：

```bash
uv run wonder-research start HK.00728 --baseline "$BASELINE"
uv run wonder-research save-draft "$RUN" --input packet.json
uv run wonder-research compare "$RUN"          # 预览
uv run wonder-research validate "$RUN"
uv run wonder-research finish "$RUN"           # 生成 review.md
uv run wonder-research backup --output data/backups/stage6-smoke
```

本阶段实施后的真实 smoke（沿用阶段 5 的 HK.00728 完成 run 作为 baseline，草稿内容相同，故差异为 0）：

```text
save-draft → ok=true，source_count=7，issues=[]
validate   → exit 0
compare    → comparable=true，score_total.diff=null（基准总分为 null），
             changes={answers:0, gates:0, moats:0}，new_sources=[]，reused=7
finish     → completed；artifacts=[company.md, research.json, review.md]
review.md  → “差值：不适用（缺少分值或不可比）”；标注旧来源保留原始时间
backup     → output=data/backups/stage6-smoke，file_count=19，missing=[]，
             manifest_sha256=4824850d…
```

恢复演练（把备份复制到临时根）：

```text
restored 19 files
history HK.00728 → 2 个 completed run 可读
finish <run>     → 对已完成 run 只核对产物哈希（含 review.md），exit 0
source-read      → locator lines 816-818，原文可读
```

## 5. 为什么这样实现

- **不变式优先**：`finish` 只写当前 run 目录；baseline 的 `company.md`/`research.json` 及其哈希保持不变。复评是新增记录，不是覆盖。
- **未知不等于零**：`_score_diff` 在任一侧 `null` 时返回 `null`，`review.md` 显示“不适用”；总分也遵循同一规则，不把未知折算进差值。
- **新旧证据分列**：按 run_sources 集合差把来源分为“本次新增/沿用基准”，并附真实获取与发布时间，避免把旧证据说成本次新取得。
- **备份一致性**：SQLite backup API 在写锁内运行，业务写入被排除；只复制已归档来源与完成报告，不复制缓存或运行临时文件。
- **可验证恢复**：`manifest.json` 记录每个文件的相对路径与 SHA-256、数据库哈希；恢复后 `finish` 对 completed run 会核对产物哈希，`source-read` 可读原文。
- **失败不吞掉**：锁超时等 `StorageError` 由 CLI 显式转成 exit 1 的 JSON，不让 traceback 泄漏。

## 6. 测试覆盖与不能证明的事

`tests/test_review_and_backup.py` 覆盖：无 baseline 时 `compare` 报 `no_baseline`；baseline 缺失分值不产生分差（M3 null→5 差值为 null，M1 8→9 差值为 1，总分 diff 为 null）；门闸与问题状态变化被记录；新来源与沿用来源分列且新增来源带真实 `retrieved_at`/`published_at`；`finish` 写 `review.md` 且旧报告字节与哈希不变；备份生成 DB/文件/manifest 且不覆盖已有备份、拒绝 `data/backups/` 之外的目标；备份在写锁被占用时报 `lock_timeout`；从备份恢复到临时根后历史、产物哈希与原文可用。

`tests/test_evidence_as_of.py` 另外覆盖核心证据 as_of 可用性：未知发布日期的 primary、晚于 as_of 的 primary、日期精度未到当地日终、provider `observed_at` 晚于 as_of、provider 缺 `observed_at`、以及 `observed_after_as_of=true` 均不能支撑核心结论，报 `ineligible_as_of_support`；资料仍保留归档。

这些测试**不能**证明：模型给出的变化解释正确、复评结论正确、备份能抵御磁盘损坏或恶意篡改，或任何证券值得投资。`review.md` 只保证结构差异与产物一致性。

## 7. 小练习

1. 用 `start --baseline` 新建一次复评，改动草稿里一个门闸结果并填一条 `review_explanations`，`save-draft` 后先 `compare` 预览，再 `finish`；查看 `review.md` 是否只标出该变化。
2. 把 `data/backups/<name>/files/<某个来源>` 改一个字节，恢复到临时根后 `source-read` 或哈希校验应发现不一致。
3. 备份期间在另一终端对同一 root 执行写命令，观察是否被写锁拒绝而非静默并发。

## 8. 已知限制与下一阶段

- `compare` 只做结构与数值差异；变化原因文字来自草稿的 `review_explanations`（本阶段没有模型自动生成，留空时 `review.md` 明确标注）。
- `backup` 是本地一致快照与文件拷贝，不做云同步、增量备份、定时任务或加密。
- 未实现估值、附记正文、MCP/LangGraph。
- 下一阶段（阶段 7，本版本必做）：Python 直接调用 DeepSeek，从已归档来源构造受限上下文生成 `ResearchDraft`，只 `save-draft`+`validate`，由用户显式 `finish`；凭据仅从环境变量读取。
