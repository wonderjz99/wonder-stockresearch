# 阶段 15：Writer 问题对齐与校验失败反馈

本阶段处理两类容易被误认为“报告已完成”的问题：模型按编号错放回答，以及自然语言研究校验失败时终端不显示具体原因。

## 发生了什么

只读检查了这次中国电信 run `9f0fd795-4e44-4ec4-b523-fd4a71412233`。它仍处于 draft，没有正式报告。Writer 的 JSON 结构校验通过，但业务校验返回两项 `missing_authoritative_support`：Q01 和 M1 使用了 Futu profile 快照，并把证据关系标成 `context`。该快照观测时间晚于 run 的 `as_of`，因此不能支持核心结论；校验拒绝它是正确的。

同一草稿中 Q03 写了经营/投资/融资现金流，Q04 写了资产负债表指标。原 Writer prompt 只要求覆盖 Q01–Q24 编号，没有提供对应问题的全文，所以模型可能按自己记忆给编号配题意。这属于写作指导不足；Pydantic 结构校验无法判断中文回答是否真正回答了题目。

## 统一的问题定义

`src/wonder_stockresearch/questions.py` 维护 Q01–Q24 的完整文本，正式报告和 Writer 从同一份定义读取。这样避免报告标题和模型提示使用不同版本的问题措辞。

Q03 和 Q04 另有明确范围约束：

- Q03 聚焦资本开支金额、资本开支占收入比例、对应期间及计算口径。利润率、ROE、净利润、EPS 或泛泛现金流不能替代资本开支分析。资料不足时应写明缺口，而不是用其他指标填满。
- Q04 聚焦技术替代/产品变化发生的速度、受影响业务，以及对竞争地位的具体影响。EPS、利润率、估值或资产负债表指标本身不回答技术替代问题。

Writer 还被要求逐项核对编号、题意、陈述、数值和引用是否一致。Q01 优先使用截止时点前发布的一手披露；M1 只有在存在可核验的单位成本或成本结构优势时才给非空分数。网络覆盖或规模大不自动等同于成本优势。

提示不能构成语义正确性的数学保证。最终的 `validate` 仍负责可程序验证的边界：结构完整、来源存在/关联、引用资格和时间适用性；它不会仅靠关键词断言答案语义正确。真实模型效果需用归档的三家电信公司语料再次验证。

## 校验失败时的终端反馈

固定自然语言研究若 `validate` 失败，现在会在终端显示：

```text
草稿已生成但未通过证据校验；尚未生成或冻结正式报告。
run_id: <run_id>
- draft.answers[0].statements[0] [missing_authoritative_support]：需要 primary 或 provider 的 supports 引用
```

草稿保留为 draft，run_id 可用于查看状态、读取来源或重新生成。命令不会因为结构校验通过就 finish，也不会把 context 引用改写成 supports。stdout 为管道/重定向时仍返回 `{ok:false,data,issues}` JSON，exit code 为 2。

## 验证

离线测试覆盖 Writer prompt 中的完整 Q03/Q04 题目、专项禁止替代规则、M1 证据提示，以及 TTY 失败详情和非 TTY JSON 错误返回。离线测试能证明提示构造和错误输出，不代表模型一定遵循提示。

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/ruff check .
```

## 当前 run 和下一步

本阶段没有调用 DeepSeek，也没有覆盖失败 run 中已有的草稿。用户如决定重试，可对现有 draft run 执行：

```bash
uv run wonder-research --root . write-draft 9f0fd795-4e44-4ec4-b523-fd4a71412233 --provider deepseek
uv run wonder-research --root . validate 9f0fd795-4e44-4ec4-b523-fd4a71412233
```

`write-draft` 会替换该未完成 run 的现有草稿，所以是否实际重跑需由用户决定。只有 `validate` 通过后，才可显式 `finish`。历史 completed 报告保持冻结；如要看到新问题对齐版式，需要生成一份新 run。
