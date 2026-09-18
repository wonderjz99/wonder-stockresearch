# 阶段 1：离线公司研究闭环

## 1. 本阶段解决什么问题

这一阶段把一份离线 JSON 研究包变成两个可检查的产物：结构化校验结果，或一份临时 Markdown 公司报告。它只处理虚构公司 `SYNTHETIC` 的教学材料，不查询真实公司、账户、行情或网络服务，也不创建 SQLite 记录。

阶段 2 会把已校验草稿保存为可追溯的历史版本；所以本阶段的 `render-packet` 明确标注“离线样例/未正式归档”。

## 2. 数据流与调用链

```text
离线 JSON
    │
    ▼
ResearchPacket.model_validate()
    │  Pydantic：字段、类型、范围、未知字段
    ▼
validate_packet(packet)
    │  覆盖、重复编号、来源引用、时间、关键冲突
    ├── validate-packet → JSON stdout
    └── render_company(packet) → Markdown（仅 root/data/）
```

`wonder-research` 只解析命令、读取 JSON、调用纯函数并打印 JSON。业务规则不藏在 CLI 中，所以阶段 2 可复用同一套模型与校验。

## 3. 实际文件与入口

- `pyproject.toml`：Python 3.11、Pydantic、pytest、ruff 和 `wonder-research` 入口。
- `src/wonder_stockresearch/models.py`：严格输入契约，例如 `ResearchDraft`、`Source`、`Statement`、门闸和护城河评分。
- `src/wonder_stockresearch/research.py`：`validate_packet(packet)`、`derive_score_total(draft)` 与质量状态派生。
- `src/wonder_stockresearch/reports.py`：`render_company(packet)`，只渲染已解析的对象。
- `src/wonder_stockresearch/cli.py`：`main(argv)` 与两个阶段 1 子命令。
- `tests/fixtures.py`：完整虚构 `SYNTHETIC` 包，含 Q01–Q24、六门闸、M1–M6、两个来源、反方观点与一个 `null` 分数。

输入包形状为：

```json
{
  "draft": {"schema_version": 1, "ticker": "SYNTHETIC", "...": "完整 ResearchDraft"},
  "sources": [{"id": "UUID4", "kind": "primary", "...": "完整 Source"}]
}
```

不能添加 `valuation`、`target_price` 或 `action`：所有模型拒绝未知字段，避免估值混入公司评分输入。

## 4. 运行方式与结果

在项目根目录执行：

```bash
uv sync --group dev
uv run pytest tests/test_cli.py -q
uv run wonder-research --help
```

`tests/test_cli.py` 会在临时项目根中把完整 `SYNTHETIC` fixture 序列化为 JSON，然后实际调用：

```text
wonder-research --root <项目根> validate-packet --input packet.json
wonder-research --root <项目根> render-packet --input packet.json --output data/teaching-report.md
```

前者成功时 stdout 是 `{"ok": true, "data": {"valid": true}, "issues": []}`。后者生成 `data/teaching-report.md`，含 Q01–Q24、G1/G2/G3/G5/G6/G7、M1–M6、反证、来源清单及“估值部分尚未实现”。输出路径不在 `data/` 时，命令返回退出码 2 且不写文件。

真实输入可来自文件或 `--input -` 的标准输入；研究正文应使用 UTF-8 JSON 传入，不能用 shell 字符串拼接。

## 5. 为什么这样实现

Pydantic 适合边界校验：分数只能是 0–10 或 `null`，数值事实必须有单位、期间、口径和引用；`extra="forbid"` 把未定义字段当输入错误。它只验证数据形状，不能判断文字是否为真。

第二层纯函数处理跨字段规则：24 个问题、六个门闸、六项护城河必须各出现一次；引用的 `source_id` 必须在来源清单中；已回答问题、通过/失败门闸和非空评分必须有 `primary` 或 `provider` 的支持性引用；已知发布日期晚于 `as_of` 的来源会被拒绝。

`derive_score_total` 采用“全有或全无”：任一维度为 `null` 时总分也是 `null`，而非把未知当作 0。`null` 是“尚未评估”；0 是“已评估但未见优势”。

## 6. 测试覆盖与不能证明的事

自动化测试覆盖：

- 估值字段、非法评分和空数值单位被模型拒绝；
- 完整 `SYNTHETIC` 覆盖通过，`null` 总分保持未知；
- 重复问题编号与不存在的来源引用产生结构问题；
- Markdown 保留教学标签、估值延后说明和 `null` 语义；
- CLI 的成功 JSON、`data/` 内写入和越界输出拒绝。

这些测试不能证明来源原文真实存在、引用语义正确、公司的业务判断正确，或任何证券值得买卖。本阶段没有正式归档、数据库、下载、富途连接或估值模型。

## 7. 小练习

在 `tests/fixtures.py` 中，把 `answers` 里第二个项目的 `question_id` 临时改成 `Q01`，然后运行：

```bash
uv run pytest tests/test_research.py -q
```

预期：完整包测试失败，因为校验同时报告重复 `Q01` 与缺少 `Q02`。改回后测试恢复。另一个练习是把 Q09 数值的 `unit` 改为空字符串；Pydantic 会在解析阶段拒绝它，不会让“18.5”成为无单位数字。

## 8. 已知限制与下一阶段

当前 `Source.raw_path` 只是离线输入元数据，程序不检查文件、不会下载内容，也不保存任何内容。临时 Markdown 可被覆盖，且没有 run ID、历史、草稿状态或事务。

阶段 2 将引入 SQLite、`data/` 布局、股票清单、草稿与研究历史；届时报告只能由已校验草稿正式导出，完成记录不可覆盖。
