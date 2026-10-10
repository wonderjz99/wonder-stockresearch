# 固定研究流程可靠性修复

## 1. 本次解决的问题

自然语言入口可以完成采集，但真实腾讯研究在 Q01–Q04 连续失败，并报
`invalid_section_coverage（实际为空）`。数据库记录显示模型两次都正常结束且输出数千 token，
所以“空”只是旧解析器的二次误报：模型返回了损坏 JSON，解析器又从内部捞出
Statement/Gap 对象，最后把它们当成没有 Answer 的分组。

本次同时检查了后续 `validate → finish`。真实运行还暴露出两类问题：综合 Writer 偶尔把
`gates`/`moats` 写成按 ID 索引的对象；语义审查通过后，Python 的确定性引用校验仍可能发现
`missing_authoritative_support`，旧流程不会把这类硬错误送回定向修订。

## 2. 修复后的调用链

```text
chat 确认
→ start / collect
→ 六组 AnswerSection 对象
→ 综合判断
→ 保存草稿
→ DraftReview
→ 合并语义 error 与 Python 硬校验 error
→ 最多五个单元各修订一次
→ 最终 DraftReview + validate
→ 等待用户确认 finish
```

任一步失败时，chat session 保存 `run_id`、失败步骤和重试动作。用户输入“重试”后复用同一
run、已归档来源和输入哈希匹配的成功分组，不重新创建 run，也不重复 collect。

## 3. 关键实现

- `writer.AnswerSection` 是 Prompt、JSON Schema、解析器、缓存和修订共用的顶层对象契约，
  统一为 `{"answers": [...]}`。
- 损坏的外层 JSON 只允许按“连续完整 Answer 对象”兼容；不会扫描内部任意 JSON 对象，
  因而保留真实的语法错误位置。
- `invalid_json` 的唯一一次修复从原证据输入重新生成，不复制损坏响应；结构或覆盖错误仍只能
  修复结构，不能新增证据外事实。
- EvidencePack 的完整片段只发送一次；`question_chunks` 改为 `source_id + locator` 索引，
  腾讯 Q01–Q04 的真实输入由约 30,700 降到约 21,800 prompt token。
- 综合判断只对可无损识别的精确 G/M ID 映射转成列表，仍由 `ResearchDraft` 严格校验字段。
- 语义审查 error 与 `validate` 的草稿硬错误汇入同一定向修订单元。缺权威支持时，Writer 只能
  删除无支持陈述，或降为 partial/missing 并给完整 gap；不能把 context 偷改成 supports。
- `chat` 捕获 Writer、存储和 finish 失败，返回 `session_id`、`run_id`、`failed_step` 和 issues；
  非 TTY 仍使用 `ok=false` 与 exit 2。

## 4. 真实验收

2026-10-06 使用项目现有 `.env`、只读 OpenD/HKEX 采集和 DeepSeek 完成三条自然语言流程：

| 公司 | ticker | run_id | 结果 |
| --- | --- | --- | --- |
| 腾讯控股 | HK.00700 | `36312547-ca74-4e2d-9b6e-a8b98e9e22a6` | 24 Q / 6 Gate / 6 Moat，validate 通过 |
| 中国移动 | HK.00941 | `eac6fecc-3e80-4524-af4b-c4b3ae677c7a` | 非法 JSON 断点恢复与硬校验修订后 validate 通过 |
| 中国电信 | HK.00728 | `81f8aa26-41f2-41f7-a445-65472ecc8cc5` | G/M 映射归一化与 Q09 定向修订后 validate 通过 |

三条原 run 均保持 `draft`。另在 `data/tmp/fixed-flow-check/freeze/` 的隔离数据库副本执行
`chat 确认定稿` 与重复 `finish`，三家公司均生成 `company.md`、`research.json`、
`quality-review.json`；24/6/6 覆盖、review/draft hash、产物哈希和重复定稿均通过。

## 5. 测试与限制

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

实际结果为 `174 passed` 和 `All checks passed!`。测试覆盖对象契约、损坏 JSON、空/重复分组、
截断响应、缓存覆盖、修订缓存、硬校验进入修订、chat 同 run 恢复、finish 失败反馈和非 TTY
退出码。

用户原命令使用的 extras 路径也逐一执行了只读验证：

```bash
UV_CACHE_DIR=/private/tmp/coding_agent_uv_cache \
  uv run --extra observability wonder-research --root . validate RUN_ID
```

三条 run 均返回 `{"ok":true,"data":{"valid":true},"issues":[]}`。

自动校验通过仍不表示研究判断正确。三份草稿的 warning、数值口径和结论仍需人工阅读；只有
用户对原 run 明确确认 `finish` 后，项目才会冻结正式报告。

## 6. 后续证据与评分修正

2026-10-06 后续检查发现，旧综合 Writer 只接收 M1–M6 编号而没有维度定义，导致部分草稿把网络效应、
品牌、转换成本、规模经济与监管壁垒错位。当前模板把每个维度的定义、相关问题和评分锚点传入综合判断，
并要求 M1 的相对成本证据与其他维度分开处理。Provider JSON 的长字段也改为截取命中词附近内容，避免
富途主营构成、股东、管理层和分红记录因截断而不可见。

M1–M6 仍以证据分为准。为使含缺口的报告也能用于当前排序，报告另列不改写单项分数的暂定总分、覆盖数
与可能区间；具体口径见 [护城河评分与资料补全](19-moat-scoring-and-evidence-recovery.md)。
