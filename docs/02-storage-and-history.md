# 阶段 2：SQLite、草稿与股票历史

## 1. 本阶段解决什么问题

阶段 1 只能把一份离线 JSON 变成临时报告，程序一退出什么都不剩。这一阶段加入持久化：每次研究成为一个有编号的 **run**，可以先存草稿、再校验、最后定稿；定稿报告写入项目 `data/` 下的固定目录，数据库记录它的状态和产物哈希。完成后不可覆盖，想更新就新开一条 run，并用 `--baseline` 关联旧研究。

仍然全部离线：不下载资料、不接富途、不做估值。数据库和文件操作都限制在项目 `data/` 下。

## 2. 数据流与调用链

```text
CLI（cli.py：解析参数、打印 JSON）
    │  只做参数解析/序列化，不藏业务规则
    ▼
research.py（业务服务：watch / start / save_draft / validate_run / finish_run / resume / status / history）
    │  身份与状态检查、调用阶段 1 的 validate_packet、渲染报告
    ▼
storage.py（持久化：连接、建表、事务、写锁、文件路径）
    ├── SQLite：data/wonder.db
    └── 文件：data/stocks/<ticker>/runs/<时间>-<run_id>/{company.md, research.json}
```

阶段 1 的 `models.py`（严格输入）和 `reports.py`（固定 Markdown）被直接复用；`render_company` 新增 `archived=True` 分支，正式报告不再标“离线样例”。

## 3. 实际文件与入口

- `src/wonder_stockresearch/storage.py`（新）：`initialize` 建库、`connect`/`open_db`、`project_lock` 写锁、`run_dir`/`sources_dir` 路径、`insert_run`/`update_draft`/`mark_run_completed` 等 SQL。
- `src/wonder_stockresearch/fixed/research.py`（扩展）：纯校验之上新增服务函数，统一入口为 `watch_add`、`watch_remove`、`watch_list`、`start_run`、`save_draft`、`validate_run`、`finish_run`、`resume_run`、`get_status`、`get_history`；校验失败抛 `ResearchError`（退出码 2 表示身份/校验拒绝，1 表示 I/O 或持久化错误）。
- `src/wonder_stockresearch/cli.py`（重写）：新增 `watch`、`start`、`status`、`history`、`save-draft`、`validate`、`finish`、`resume`。
- `src/wonder_stockresearch/models.py`（扩展）：抽出 `validate_ticker`，新增 `REPORT_KINDS`、`RUN_STATUSES`。
- `src/wonder_stockresearch/fixed/reports.py`（扩展）：`render_company(packet, *, archived=False, run_id=None)`。
- `tests/test_storage.py`（新）：8 个阶段 2 测试。

四张表（`PRAGMA user_version = 1`）：

```sql
stocks(ticker PK, name, watched, created_at)
runs(id PK, ticker FK, kind, parent_run_id FK, as_of, baseline_run_id FK,
     status, draft_json, schema_version, created_at, updated_at,
     completed_at, error_json, artifact_hashes_json)
sources(id PK, ticker FK, kind, metadata_json, sha256, raw_path, text_path,
        retrieved_at, source_json)
run_sources(run_id FK, source_id FK, PRIMARY KEY(run_id, source_id))
```

研究内容仍以 JSON 存在 `runs.draft_json`。`source_json` 保存来源完整快照，其余列用于检索；这是对技术规格 sources 表的一处工程补充，产品语义不变。

## 4. 最小可运行示例

在项目根目录执行（下面 `$DEMO` 是任意一个含 `pyproject.toml` 的演示项目根）：

```bash
uv run wonder-research --root "$DEMO" watch add SYNTHETIC --name 教学公司
uv run wonder-research --root "$DEMO" start SYNTHETIC
# 记下返回的 run_id
uv run wonder-research --root "$DEMO" save-draft "$RUN" --input packet.json
uv run wonder-research --root "$DEMO" validate "$RUN"
uv run wonder-research --root "$DEMO" finish "$RUN"
uv run wonder-research --root "$DEMO" history SYNTHETIC
```

实际运行结果（阶段 2 实现后实跑，摘录）：

```text
watch add → {"ok": true, "data": {"ticker": "SYNTHETIC", "watched": true}, "issues": []}
start     → data.run_id = 9e54661e-...；status=draft、kind=standard
status    → answer_count=24、source_count=2、score_total=null、quality_status=evidence_incomplete
validate  → {"ok": true, "data": {"valid": true}, "issues": []}
finish    → run_dir=data/stocks/SYNTHETIC/runs/20260918T135947Z-9e54661e-.../
            artifacts={company.md: 4d254f...、research.json: 3805b7...}
history   → 1 条 completed run
```

生成的正式报告开头：

```text
# 公司研究 — Standard 公司部分

> 状态：正式归档（run 9e54661e-...）。完成仅表示通过结构与来源检查，不代表投资判断已证实。
```

## 5. 为什么这样实现

- **状态与产物一致性**：`finish_run` 的顺序是“校验 → 写 company.md/research.json → 再更新数据库为 completed”。崩溃在文件与提交之间时 run 仍是 draft，重试可安全重建；对 completed 的 run 重试只核对哈希，不重写。
- **写锁**：所有写操作经 `project_lock`（`fcntl` 排他锁，5 秒超时）。持锁期间不做网络下载，避免长时间占锁。
- **事务**：`sqlite3` 以 `isolation_level=None` 手动控制，写操作用 `BEGIN IMMEDIATE ... COMMIT`，失败 `ROLLBACK`；`PRAGMA foreign_keys=ON` 保证 run/source 归属有效。
- **原子文件替换**：`write_atomic` 先写 `.tmp` 再 `replace`，避免留下半截报告。
- **映射关系**：`ResearchPacket` 的 `draft` 存 `runs.draft_json`，`sources` 存 `sources.source_json` 并连到 `run_sources`；`render_company` 负责把对象渲染成固定 Markdown，而不是把 Markdown 当事实源。
- **预留而非实现**：`runs.kind`/`parent_run_id` 为附记（addendum）和专题（topic）预留，`start` 本阶段只产生 `standard`；估值内容以后放附记，绝不进入公司分数。

## 6. 测试覆盖与不能证明的事

`tests/test_storage.py` 覆盖：

- 重启后（新连接）能读回草稿，`status` 显示 24 问、2 来源、总分未知；
- 同日两次 run 目录不冲突，`history` 都能列出；
- completed run 不可覆盖，重复 `finish` 哈希不变；
- 不完整草稿 `finish` 被拒且状态仍为 draft；
- 跨股票 baseline 被拒，同股票 baseline 可关联；
- 只有 failed 能 `resume`；
- 产物写入失败时 run 标 failed 而非 completed；
- CLI 的 watch→start→save-draft→status→finish→history 与 JSON 契约。

这些测试**不能**证明：来源原文真实存在（阶段 2 不校验字节）、引用语义正确、公司判断正确，或任何证券值得买卖。它只证明记录结构、状态转换和文件/数据库的一致性。

## 7. 小练习

1. `start` 一条新 run，不 `save-draft` 就直接 `finish`。预期：退出码 2，`issues` 里的 code 为 `empty_draft`，run 仍是 draft。
2. 在 `tests/fixtures.py` 里删掉 Q24，然后用该 packet 走 `save-draft` 再 `finish`。预期：`save-draft` 退出码 0 但返回 `missing_id`（保存不阻止）；`finish` 退出码 2、报告不生成。
3. 把同一 run `finish` 两次。预期：第二次返回相同 `run_dir` 与 `artifacts`，不产生新版本。

## 8. 已知限制与下一阶段

- 来源只有元数据，没有真实字节归档；`source-add`/`source-read` 与 PDF/网页提取属于阶段 3。
- 没有真实网络、富途、公告发现（阶段 4），没有 OpenCode skill（阶段 5）。
- `--baseline` 只保存关联，不生成 `review.md`、不做差异比较（阶段 6）。
- 附记正文、专题报告、估值（Q25–Q28/G4）均未实现；仅预留了 `kind`/`parent_run_id`/估值位置。
- 写锁使用 `fcntl`，非目标平台 Windows 本版不支持。
