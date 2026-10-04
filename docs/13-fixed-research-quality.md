# 阶段 16：固定研究质量流水线

## 1. 目标与边界

阶段 16 把一次性 Writer 改成可恢复、可审查的固定研究流水线。模型仍只负责结构化写作和语义审查；Python 负责来源资格、证据定位、可信计算、schema、调用预算、持久化、评分与正式报告冻结。本阶段不执行模型临时生成的代码，不引入 LangGraph、向量数据库或新联网步骤。

## 2. 默认 staged 流程

```text
六组 EvidencePack
→ Q01–Q04 / Q05–Q08 / Q09–Q12 / Q13–Q16 / Q17–Q20 / Q21–Q24
→ 综合 gates / moats / challenge / conclusion
→ 保存 ResearchDraft
→ 全局 DraftReview
→ 最多 5 个单元各定向修订一次
→ 最终 DraftReview
```

`write-draft RUN_ID` 默认使用 `staged`；`--strategy one-shot` 只用于回归比较，真实 schema v6 及以后的 standard run 同样必须通过语义审查。每次 staged 尝试的模型调用硬上限为 14；超过 5 个单元同时失败、预算耗尽或最终审查仍有 error 时立即停止，run 保持 draft。

## 3. EvidencePack 与可信计算

`fixed_quality.py` 为每组建立有界证据包。每个 `EvidenceChunk` 保存 `source_id`、真实 locator、来源类别、supports 资格、文本或结构化字段、period/currency/unit/accounting standard/dataset、内容哈希和截断状态。Provider JSON 按相关节点抽取；PDF/文本按页或行切块并用中英文关键词确定性排序。每个问题先保留 2 个片段，再视预算扩展到 4 个；每组最多 18 个片段、每来源最多 8 个片段、总量最多 32000 字符；没有命中时显式记录 gap。

分组答案与综合判断的引用还会由 Python 对照实际 EvidencePack：引用必须使用交给模型的 `source_id + locator`，且 `support_eligible=false` 的片段不能标记为 `supports`。命中旧缓存时也会复核；只有失效的分组会重写。

可信计算使用 Decimal，当前包括净利润同比、资本开支/收入、ROE 序列、覆盖年数、ROE 超过 15% 的年份数，以及最小值、最大值、中位数和总体标准差。资本开支/收入优先使用年报中带币种、单位和当年语义的 Capex 披露；只有名称和口径明确的现金流项目才另列为 `cash_capex_proxy`，不会冒充已披露的 Capex。结果保存公式版本、输入、期间、币种、来源引用、缺失期间和不可比原因。模型只能解释计算结果。

## 4. 语义审查和冻结门闸

`DraftReview` 只允许五类问题：题意对齐、证据支持、数值一致、跨章节一致和范围违规。审查必须完整列出 Q01–Q24、六个 Gate 和 M1–M6；引用必须属于当前 run；`draft_hash` 必须匹配当前草稿。error 阻止 finish，warning 可以随报告归档。

Python 会拒绝越界审查引用，并只对可机械识别的审查误报调整严重度：例如同一条 `numeric_consistency` error 又明确说“与披露/可信计算一致”或“换算正确”，会降为 warning。`NumericFact` 已逐项保存数值、期间、口径和 `supports` 引用；审查器不能因为同一数字、分子、分母或绝对额没有在自然语言句子中重复出现而报 error。PDF 的 `page N` 也是有效 locator，不能仅因它不是 Provider 的 `item_list` 定位而报 error。Python 不会根据模型隐藏推理推断事实，也不会放行实际数值差异、来源越界或题意遗漏。

`answered` 表示该题没有未解决的证据缺口，因此 `gap` 必须为 `null`。`partial` 或 `missing` 必须保留完整 gap。该规则同时由分组缓存复用和 `validate` 执行，避免旧草稿因状态标签错误而进入 finish。

schema v6 新增：

- `draft_sections`：按 `run_id + section_key + input_hash` 保存成功分组，失败后重试复用已完成组。
- `draft_reviews`：保存草稿哈希、结构化审查、reviewer 类型、模型和模板版本。

schema v7 新增：

- `source_page_fts`：SQLite FTS5 页级/固定行段索引，保存 `source_id`、真实 locator、内容和内容哈希。索引只来自当前 run 已归档的非 Provider 文本；内容哈希变化时重建对应来源，不能联网也不会改变来源原文。

新建 standard run 缺审、旧审查哈希失效或审查失败时，`validate` 分别返回 `semantic_review_missing`、`semantic_review_stale`、`semantic_review_failed`。schema v5 及更早的历史 run 沿用旧规则。通过审查后 `finish` 额外冻结 `quality-review.json` 并写入产物哈希。仓库的 teaching fixture 只用于旧生命周期回归，不代表真实公司 run。

## 5. CLI

```bash
wonder-research write-draft RUN_ID [--strategy staged|one-shot]
wonder-research review-draft RUN_ID [--provider deepseek | --input review.json]
wonder-research draft-read RUN_ID
wonder-research review-read RUN_ID
wonder-research draft-preview RUN_ID
wonder-research status RUN_ID
wonder-research validate RUN_ID
wonder-research finish RUN_ID
```

`draft-read` 和 `review-read` 只输出当前保存的 JSON，不调用模型、不生成报告、不改变 run。`draft-preview` 在 `data/tmp/draft-previews/<RUN_ID>/` 写入 `draft.md` 与已有的 `quality-review.md`，明确标为未定稿预览，不改变 run。`status` 返回分组缓存、14 次预算使用情况、当前草稿哈希和审查状态。人工导入审查使用与模型相同的 `DraftReview` schema，不能绕过覆盖、哈希和引用校验。

## 6. 真实验收结果

### 6.1 16.1 定向证据恢复

16.1 在不新增联网或数据源的前提下，修复了富途嵌套 `report_list → item_list` 的期间、币种和会计准则继承，并将现有年报建立为 SQLite 页级索引。三份现有 run 的离线复核结果如下：

| 公司 | 页级索引条目 | ROE | 年报 Capex/收入 |
| --- | ---: | --- | --- |
| 中国移动 | 1,044 | 2016–2025，10 年 | 2021–2025，5 年 |
| 中国联通 | 1,220 | 2016–2025，10 年 | 2021–2025，5 年 |
| 中国电信 | 1,570 | 2016–2025，10 年 | 2021–2025，5 年 |

中国电信 2021–2025 的年报 Capex 分别为 867.23、925.28、988.38、935.13、803.64 亿元人民币，均有页码定位；这说明原来的“部分完成”主要是字段和检索遗漏，不是数据缺失。可信计算输入也成为 Writer 可引用的受控证据，仍只允许引用其实际 `source_id + locator`。

三家公司均使用阶段 13 的 20 条归档来源建立新 schema v6 run，未自动 finish：

| 公司 | run_id | 覆盖 | 最终审查 | 最后一次尝试 |
| --- | --- | --- | --- | --- |
| 中国移动 | `fe6ecce2-de69-4b24-84c6-be67780b6d58` | 24 Q / 6 Gate / 6 Moat | 0 error / 20 warning，`validate` 通过 | 6 calls、311254 tokens、1 个修订单元；先前的 `answered_with_gap` 已在分组恢复中修正 |
| 中国联通 | `e491dee8-2f95-4aac-8087-cac4f37a7e46` | 24 Q / 6 Gate / 6 Moat | 0 error / 20 warning，`validate` 通过 | 11 calls、389702 tokens、0 个修订单元 |
| 中国电信 | `e5d20c8c-2bd5-4fa3-a8d3-db873a79d52b` | 24 Q / 6 Gate / 6 Moat | 0 error / 20 warning，`validate` 通过 | 8 calls、371399 tokens、4 个修订单元；一次综合引用越界被拦截后恢复成功 |

真实运行验证了分组缓存、断点恢复、引用范围校验和审查归一化。三家公司的结构、来源边界、状态语义与当前审查均通过，且都保持 draft，供人工检查 warning。中国移动的 Q09 旧标签被 `answered_with_gap` 规则拦截；网络恢复后的缓存式重试修正了受影响组，并重新完成审查。三份结果都不能把自动审查通过等同于研究判断正确。

一次性 Writer 基线的已记录样例为：中国移动 139371 tokens，中国电信 142880 tokens。staged 的单次完整路径虽然降低了单组上下文，但综合审查与修订使总 token 仍明显偏高，后续应优化 Reviewer 输入与证据定位，不能只比较调用次数。

## 7. 已知限制

- PDF/文本证据使用确定性关键词，不做语义向量检索；某组可能因关键词未命中而保守返回 missing。
- Reviewer 仍可能产生不属于上述确定性情形的误报；这类 error 会保留 draft，不能由 Python 臆断放行。
- 三份草稿均已通过自动门闸，但仍需用户按问题、数值口径和 warning 查看后决定是否 finish。阶段 17 需等本阶段真实验收完成后再开始。
