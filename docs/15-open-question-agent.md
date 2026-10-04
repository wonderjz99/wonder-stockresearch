# 阶段 18：开放问题 Agent、记忆与受限临时计算

## 1. 为什么固定研究不使用自由 Agent

标准 `ResearchRun` 继续走确定性流程：EvidencePack、六组 Writer、Python 可信计算、语义审查和 `finish` 门闸。它不运行模型生成的代码，也不引入多 Agent、LangGraph 或通用 shell。

开放问题复用现有 `agent.py` 的单 Agent loop。例如：

> 中国移动最近十年净利润增长率如何，ROE 是否稳定？

这个 loop 已有轮次/token 预算、工具白名单、重复调用检测、事件记录和 Pydantic 决策协议。阶段 18 只扩展这个 loop，不再建立第二套运行时。

## 2. 来源的双层优先级

程序分别记录“怎样取得”与“哪一个可作为更高权威依据”。两者不能混成单一关键词分数。

| 维度 | 顺序 |
| --- | --- |
| 获取 | 富途结构化数据与已有官网 HTML/结构化资料 → 官方公告、SEC/HKEX、公司官网和原生 PDF → 阶段 19 的 OCR 衍生文字 → 搜索结果发现原文后再归档 |
| 权威 | 官方申报、交易所公告、公司官网 → 有期间/币种/口径的富途结构化数据 → 合格行业/监管/专业二手来源 → 搜索发现线索 |

`Source.metadata` 现在保存 `source_rank`、`extraction_method`、`supports_eligible` 和 `reconciliation_status`。`EvidenceChunk` 同样携带这些字段。证据包按以下稳定顺序选择：可用性、问题匹配、来源权威、结构化程度、期间匹配、source_id/locator。搜索发现记录被标为 `search_discovery`，不能成为 `supports`。

财务数值的处理如下：

1. 富途的 reported 数据可先作为计算输入。
2. 同一 run 存在官方结构化明细时，按科目、期间、币种比较。
3. 相同数值记录为 `matched`；没有官方明细记录为 `provider_unverified`，在 `status` 中保留 warning。
4. 不同数值记录为 `conflict`，官方值在证据选择时优先。草稿必须在 `conflicts` 中明确记录并说明已解决；否则 `validate` 和 `finish` 返回 `unresolved_source_conflict`。

PDF 原文没有可靠结构化表格时，程序不猜测其数值。阶段 19 再评估局部 OCR 与表格提取。

## 3. 富途采集调整

`market` 只采集行情快照。财务报表只从 `futu_fundamentals.statements` 请求一次，避免向同一接口重复发起请求。

`get_financials_statements` 对四类年报项目使用 `num=50` 和返回的 `next_key` 分页；程序按报告期去重，每类最多保留 20 个年度期，原始返回仍保留 period、currency、accounting standard、field_id 和接口路径。标准公司研究不再自动采集 `valuation` 或 `consensus`；估值能力以后由独立 Skill 启用。

## 4. 工作记忆与长期记忆

schema v8 新增四组表：

- `agent_scratchpads`：session 的目标、简短计划、已验证观察、计算引用、来源引用、待查问题、版本和压缩次数。
- `memory_entries` 与 SQLite FTS：`formal_report`、用户显式保存的 `memo`、以及 `episode`。
- `analysis_runs`：数据集哈希、代码哈希、限制、结果摘要、来源和工作目录。
- `source_conflicts`：富途与官方结构化数值的比较记录。

Scratchpad 自动保存，但只保存可检查的结构化摘要，不保存提示全文、密钥或模型隐藏推理。每次 follow-up 读取 Scratchpad、最近事件及最多五条同 ticker 相关记忆。上下文达到 40,000 字符预算的 70% 时，旧工具返回由 Scratchpad 摘要替换；完整事件仍在 SQLite。

长期可信记忆只能来自已完成正式报告和 `memo-save`。InteractiveReport 自动写入 `episode` 索引，状态固定为 `provisional`：它能帮助后续问题定位，但不能代替原始来源。可用以下命令检查，不需要相信模型记忆：

```bash
wonder-research --root <项目根> memory-search HK.00941 --query "ROE"
wonder-research --root <项目根> memory-read <MEMORY_ID>
wonder-research --root <项目根> ask-status <SESSION_ID>
```

`ask-status` 显示 Scratchpad revision、使用过的记忆、可信计算记录和临时分析审计。

## 5. AnalysisWorkspace

开放问题的工具顺序是：

```text
选择 Skill
→ 读取归档富途/官方数据
→ run_trusted_calculation
→ 必要时 create_analysis_dataset
→ run_analysis_code
→ read_analysis_result
→ InteractiveReport
```

可用工具为：

- `query_archived_financials`
- `search_archived_sources`
- `run_trusted_calculation`
- `create_analysis_dataset`
- `run_analysis_code`
- `read_analysis_result`

数据集位于 `data/tmp/analysis/<session>/<execution>/input.json`，是最小化的只读 JSON 副本。每个 session 最多三次分析执行。代码只接受受限 Python AST：没有 `import`、属性访问、动态执行、shell、网络、SQLite 或任意文件读取；运行子进程不继承 API Key，限制为十秒、256 MB、64 KB 输出和有限文件描述符。代码必须赋值 JSON 兼容的 `result`。

这个 AST 运行器提供应用层限制和审计，不取代操作系统级隔离。需要处理不可信的大型代码、二进制工具或真实网络时，阶段 20 的容器/E2B 评估才是对应路径。

临时结果不能修改 `ResearchRun`、草稿、Gate、Moat、评分或 `finish`。只有用户显式调用 `memo-save` 才会把一个观察变成长期 Memo。

## 6. 与 Pi Agent Core 的关系

Pi 的 `transformContext`、事件流、工具生命周期和会话压缩是本阶段的参考来源：本项目使用了相同的问题拆解方向，但由 Python 保持工具权限、SQLite、来源定位和报告冻结。Pi 本身不提供文件、进程、网络或凭据隔离，因此当前不迁移 runtime。

## 7. 后续路线和评估门槛

### 阶段 19：新数据源与 PDF/OCR

- 接入 SEC `submissions`/XBRL `companyfacts`，并评估富途的高管、股东、内部人、财报和公告发现接口。
- 官网 HTML/XBRL 优先；只对原生 PDF 提取失败或表格严重损坏的页面局部 OCR。
- 用同一组中英文年报页比较 OCRmyPDF、Docling、PaddleOCR PP-StructureV3 与 MinerU，按文字/表格正确率、页码映射、耗时、硬件和许可证选择一个默认后端。

### 阶段 20：受控多 Agent 与 Pi 对比

先用二十道开放问题比较当前 Python loop 与 Python loop + AnalysisWorkspace。只有独立子问题足够多，且最多三个 `asyncio.TaskGroup` worker 能提高引用完整率/正确率，或质量不降而把延迟降低至少 25%，才实现 Coordinator、Data、Document、Analysis 与 Verifier 的受控分工。

随后在相同基准比较 Python loop、Pi Agent Core + Python capabilities、Pi + 虚拟文件系统/受限 shell，以及需要强隔离时的容器/E2B。Pi 只有在会话控制、恢复、工具编排和维护成本上取得明确优势时，才替换开放问题的 orchestration 层。

### 阶段 21：专业 Skill 与语义记忆

按需求加入同行竞争、独立估值 Q25–Q28/G4、监管/市场份额/留存专题数据源。完成报告可确定性生成带来源、`as_of` 和失效条件的 Company Fact；用户偏好只允许显式配置保存，事实采用版本化和失效标记，不静默覆盖历史证据。
