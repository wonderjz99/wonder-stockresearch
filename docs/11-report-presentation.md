# 阶段 14：定稿反馈与正式报告可读性

本阶段让“报告已经完成”变成一个好找、好读、仍然可追溯的结果。它只改变展示层：不修改研究判断、证据校验、评分或正式报告的冻结规则。

## 数据流

```text
ResearchDraft + 已归档来源
          │
          ├── finish → render_company → company.md / research.json（正式冻结）
          │
          └── CLI TTY 检测 → 完成摘要与绝对文件路径
                    └── 非 TTY → 原有 JSON 输出
```

历史完成报告不会重写。只有之后新生成的 `company.md` 使用新的版式。

## 一次定稿后的终端反馈

交互终端运行 `finish`，或在 `chat` 中确认正式报告定稿后，会看到类似结果：

```text
正式报告已完成并冻结
状态：已完成
质量：完整
护城河总分：23 / 60
运行目录：/项目绝对路径/data/stocks/HK.00728/runs/...
报告：/项目绝对路径/data/stocks/HK.00728/runs/.../company.md
结构化数据：/项目绝对路径/data/stocks/HK.00728/runs/.../research.json
复评报告：/项目绝对路径/data/stocks/HK.00728/runs/.../review.md
```

路径由实际项目根和 run 目录拼成并解析为绝对路径，因此目录名含空格时也能直接复制。没有复评报告就不显示对应行。总分未评估时显示“未评估 / 60”，不会把未知分数当作零。

幂等重试时 `finish` 服务可能只返回 run 目录和文件哈希；CLI 会在分数或质量缺失时只读查询当前 run 状态。若 stdout 被重定向或连接到管道，命令仍输出 `{ok,data,issues}` JSON，方便脚本解析。长任务进度仍在 stderr。

## 报告排版和引用

`reports.py` 的 `render_company` 负责把经过校验的 `ResearchPacket` 转成 `company.md`：

- 每个 Q01–Q24 标题包含研究规格里的完整问题文本，读者不用在别处查题目。
- 答案状态、陈述类别、门闸结果、证据关系等以中文显示，例如“部分回答”“事实”“支持”。
- 数值增加千位分隔，但保留 Decimal 小数精度；不换汇、不舍入。若 unit 和 currency 相同，只显示一次。
- 来源 ID 依据完整 `source_id` 排序并映射为 `[S01]`、`[S02]` 等短编号。文末索引保留完整 ID、标题、来源类别、发布和获取时间、提取状态及地址。
- 引用以“来源 ID + locator + 关系”作为唯一性判断。完全相同的引用去重；同一来源的不同页码、字段或证据关系仍会分别显示。数值与所属陈述引用完全相同时，只保留陈述上的那条。

短编号只需要在当前报告内稳定。以后新报告的来源集合有变化时，编号可能变化；每份已完成报告包含自己的完整 ID 映射且被冻结，不会因此失去追溯能力。

## 代码入口

- `src/wonder_stockresearch/reports.py`：问题文本、枚举中文映射、数字与引用格式、来源索引以及 `render_company`。
- `src/wonder_stockresearch/cli.py`：`_finish_summary` 组装完成摘要；`_print_chat_response` 在固定研究完成时调用相同摘要；`finish` 根据 stdout 是否为 TTY 选择人类可读摘要或 JSON。
- `research.finish_run` 仍负责校验、渲染正式产物、计算哈希并冻结 run。CLI 不改写数据库或报告内容。

## 如何验证

离线测试覆盖：24 个问题文本、中文标签、来源别名映射、重复引用消除、不同 locator/关系保留、精确小数展示、TTY 与 JSON 输出、带空格的路径、缺少分数、完成重试，以及是否存在 `review.md`。

运行：

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/ruff check .
```

测试不证明模型回答的事实质量，也不验证问题与回答的语义匹配。阶段 13 样例报告中观察到的 Q03/Q04 对齐疑问，需要在独立的研究质量验证中处理；本阶段不会用排版掩盖它。

## 小练习

在教学 fixture 的 Q01 陈述中增加同一来源、不同 locator 的引用，再运行 `tests/test_reports.py`：两处定位都应保留。随后加入完全相同的引用，重复项应只显示一次。改动只影响 Markdown 呈现，不改 `EvidenceRef` 数据或校验规则。

## 当前限制与下一步

本次没有调用 DeepSeek、重写已完成报告或修改 Writer Prompt。旧报告需继续按冻结规则保留；如果要改善报告回答本身的准确性或问答对齐，应另设质量验证阶段。
