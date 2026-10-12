# 阶段 8–10、18：独立交互 Agent、记忆、临时报告与受限计算

> 本文保留阶段设计与讲解；截至2026-10-12的现行契约以[技术规格](technical-spec.md)为准，当前待办/实际状态见[PLAN](../PLAN.md)与[PROGRESS](../PROGRESS.md)，不以旧阶段限制覆盖当前规则。

2026-10-11 五题通用修复覆盖下文历史提交规则：新 Turn 冻结分析时钟及无权限澄清方法哈希，ask/chat 共用接入；已知必要时间歧义先 waiting_user，补充沿用原 Turn/预算。最终 numeric_blocks 指向本 Turn 成功 SQL 原事实，程序核对口径/期间/回执并用 Decimal 生成表和变化，正文不再平行抄数字/趋势。已有数值输入不能因函数失败声称没有计算器；可纠正提交错误预算内最多一次，已知同范围季度缺口阻止等价空查。chat 保留补充与后续失败关联。历史 Turn 不升级契约、报告不重写；详见 [问题澄清与数值回答](40-question-clarification-and-numeric-answers.md)。

2026-10-11 公司 Memo 更新：每家公司一个主备忘录；临时分析完成后 chat `save` 或无 JSON 的 `memo-save` 确定性追加原报告，同一报告快捷保存幂等。schema v14 保留旧条目和版本，memo-list 返回主入口，memo-read 支持 ticker/主 ID/旧条目 ID 和主历史版本。快捷保存允许保留无引用的缺口说明，手工 MemoRequest 仍要求引用。详见 [公司备忘录与 save](38-company-memo-save.md)。

2026-10-11 更新覆盖下文历史恢复规则：两种运行时共享 Turn 事件生成的字段/输入/错误/待办清单；字段位置未知先跨授权数据集定位。受限计算允许简单 sorted key Lambda；重复已失败调用先阻止执行并提供一次纠正反馈，累计三次可恢复错误或重复纠正机会用尽后预算内无工具收尾，重试不清零。chat 执行前保存 Agent Session/Turn 关联。实际规则和离线验证见 [字段检索与恢复](37-agent-query-and-recovery.md)。

2026-10-09 更新：用户已批准 Python 领域核心 + 项目自带 Pi SDK 问答运行时。Pi 路径已实现并在验收，默认切换须待质量门槛通过；安装、职责和已知限制见 [Pi 运行时](30-pi-agent-runtime.md)。本更新覆盖下文历史阶段中的“暂不引入 Pi runtime”限制。

同日修复后的新问题默认预算为 60,000 tokens／10 轮，覆盖下文历史默认；旧 Turn 不变。Pi 有界上下文、查询分页、工作记录与紧凑引用说明见 [可靠性修复](32-pi-agent-reliability.md)，thinking 暂不开启。

## 1. 目标

本项目有两个并行入口：

```text
固定 Prompt/CLI → 标准 ResearchRun → validate → finish → 冻结正式报告
开放问题       → AgentSession → InteractiveReport → 用户明确选择后 CompanyMemo
```

固定流程负责 Q01–Q24、公司门闸、M1–M6、证据时间边界、评分和正式报告。交互 Agent 负责回答不属于固定模板的临时问题。交互 Agent 默认不修改正式报告，也不执行 `finish`。

项目完整能力由本项目 Python 运行时提供，不依赖 OpenCode 或其他外部宿主。用户可以配置 `DEEPSEEK_API_KEY`，并使用已归档来源或富途/OpenD 数据运行 CLI；固定研究、交互 Agent 和 `chat` 均由本项目负责。用户自建 Agent 可以选择调用 CLI/API，但不是项目运行条件。

## 2. 三种产物

### ResearchRun

正式公司研究版本，保存在 `runs` 和 `data/stocks/<ticker>/runs/`。完成后不可覆盖，正式报告只能由通过校验的草稿生成。

### InteractiveReport

一次 Agent 会话的临时研究结果，保存在 `agent_sessions` 和 `interactive_reports`：

- 可以读取正式报告、历史、已归档来源和 Memo。
- 可以包含 claims、证据引用、反方、缺口和后续检查建议。
- 不参加公司评分。
- 不自动改写 `ResearchDraft`。
- 可以通过 `ask-followup` 继续追问。

### CompanyMemo

用户明确选择保存的公司长期观察，主入口保存在 `company_notebooks`，不可变条目仍在 `company_memos`：

- 由 `memo-save` 或 chat `save` 显式保存，每个 ticker 只有一个主备忘录。
- 快捷保存复制原报告全部引用、反方和缺口；无引用时明确标记，不伪造来源。手工 MemoRequest 至少带一个 `source_id + locator`。
- 按问题追加并保留历史；快捷保存同报告不重复，手工同标题仍保留条目标题版本。
- 可以被之后的 Agent 读取。
- 默认不能改变正式报告、门闸或护城河分数。

如果要把 Memo 的内容纳入正式研究，用户必须重新启动新的标准 ResearchRun，并重新通过正式证据校验。

## 3. CLI

问题输入使用 UTF-8 JSON，不把正文拼接进 shell：

```json
{
  "question": "中国电信未来两年最大的经营风险是什么？",
  "focus": "general",
  "context": "latest_completed_report",
  "allow_network": false
}
```

运行临时分析：

```bash
wonder-research --root <项目根> ask HK.00728 --input question.json
```

继续会话：

```bash
wonder-research --root <项目根> ask-followup <SESSION_ID> --input followup.json
wonder-research --root <项目根> ask-status <SESSION_ID>
```

明确保存 Memo：

```json
{
  "title": "长期跟踪事项",
  "content": "下一次复评时补充同行数据。",
  "source_refs": [
    {
      "source_id": "真实 UUID",
      "locator": "lines 120-135",
      "relation": "supports"
    }
  ],
  "tags": ["follow-up"]
}
```

```bash
wonder-research --root <项目根> memo-save <SESSION_ID> --input memo.json
wonder-research --root <项目根> memo-list HK.00728
wonder-research --root <项目根> memo-read <MEMO_ID>
```

2026-10-08 更新：新问题默认允许按需调用 `fetch_financial_data` 补取当前公司的富途只读财务资料；`allow_network=false` 禁止补取。来源属于当前 Turn，不挂入正式 run。其他联网资料仍使用固定 `collect`/`source-add`。固定计算器不限制可回答的指标，模型可以选择 SQL 或编写受限函数。默认总预算 30,000，旧 Turn 不变。详见[通用财务问答](25-agent-context-and-financial-tools.md)。

公司数据层 B：新 chat 使用 company_latest，直接 ask 可显式选择；每个 Turn 固定四类来源/解析版本，跨 Turn 复用需新建该范围并重新取得引用回执。支持基本信息与财务查询、profile 补取后索引，旧 run 范围不变。单次输入为可调整目标，Turn 总预算不变；历史版本授权不静默升级。详见 [实现、备份和真实冒烟质量限制](29-company-agent-query.md)。

`focus` 可以选择 `general`、`challenge`、`macro`、`peer`、`capacity_cycle` 或 `inventory_cycle`。这些是同一个 Agent 的受控分析角色，不是相互独立的长期 Agent；它们共享相同的只读工具、证据校验和临时报告边界。

当前自然语言入口：

```bash
wonder-research --root <项目根> chat
wonder-research --root <项目根> chat --text "帮我研究一下中国移动"
```

非严格自然语言也可以触发候选正式研究，但必须先确认。正式研究自动执行到 `validate`，通过后还要再次确认才会 `finish`。自然语言 Router 不直接调用数据库、富途或文件系统。

## 4. Agent 工具边界

Agent 的普通研究工具均为只读：

- `get_run_status`
- `list_sources`
- `read_source`
- `search_archived_sources`
- `read_report`
- `list_memos`
- `read_memo`

阶段 18 另加入受控的分析工具：

- `query_archived_financials`
- `run_trusted_calculation`
- `create_analysis_dataset`
- `run_analysis_code`
- `read_analysis_result`

这些是直接 `ask` 和历史 session 的兼容路径。阶段 19C 的新版 `analysis-workspace` Skill 改为授权 `describe_analysis_schema`、`run_readonly_sql`、`run_analysis_function` 与 `read_analysis_artifact`（同时保留归档财务查询和可信计算）；它们由 Turn 级 QueryWorkspace 执行，详情见 [Turn 级 QueryWorkspace](21-query-workspace.md)。

每个工具有 Pydantic 参数模型。旧 JSON 数据集代码只能对最小只读副本执行受限 AST；新版 SQL 只在 Turn 级 immutable 快照中执行。两条路径都不能 `import`、访问任意属性/文件、访问项目 SQLite、网络、环境变量、shell 或动态执行；Agent 不能调用任意 Python、SQL、shell、网络或交易接口。工具结果被截断后才重新放入上下文，调用事件保存在 `agent_events`，不保存隐藏推理、完整认证头或 API Key。

Agent 使用有限 JSON 协议：

```json
{"type":"tool_call","tool":"read_source","arguments":{"source_id":"...","start_line":120,"limit_lines":80}}
```

```json
{"type":"final","answer":"...","claims":[],"evidence_refs":[],"counterarguments":[],"gaps":[],"suggested_next_checks":[]}
```

```json
{"type":"ask_user","question":"是否允许先用固定 collect 采集资料？"}
```

程序校验工具名、参数、source_id 和股票归属。最终结果中的引用必须指向当前公司已归档的来源；否则 session 失败，不能生成临时报告。

## 5. 数据库和恢复

schema v4 新增：

- `agent_sessions`：问题、run 上下文、模型、预算、状态和临时结果引用。
- `agent_events`：有限调用轨迹和工具结果摘要。
- `interactive_reports`：临时报告 JSON。
- `company_memos`：公司 Memo、来源引用、来源 session 和版本。

schema v5 另新增：

- `chat_sessions`：自然语言会话历史、待确认 RoutePlan、关联 run 和交互 Agent session。

schema v8 另新增：

- `agent_scratchpads`：目标、简短计划、已验证观察、计算/来源引用、待查问题、版本和压缩次数。
- `memory_entries` 与 SQLite FTS：已完成正式报告、用户显式保存的 Memo 与标记为 `provisional` 的 InteractiveReport episode。
- `analysis_runs`：最小数据集、代码哈希、限制、结果摘要、来源引用和工作目录的审计。
- `source_conflicts`：富途与可比官方结构化数值的核对结果。

正式研究使用 `runs`；Agent 不把临时会话伪装成 `runs`。重启后可以依靠 session ID、SQLite 和 `data/` 恢复状态，不依赖 OpenCode 私有会话数据库。

Scratchpad 自动记录可检查的工作状态，不保存提示全文或隐藏推理。follow-up 只加载 Scratchpad、最近事件和最多五条同 ticker 的相关记忆；上下文接近 40,000 字符预算的 70% 时，以结构化摘要替代旧工具返回，完整轨迹仍可在 SQLite 查看。完成报告和 `memo-save` 才是长期可信记忆；临时报告 episode 只作定位线索，不能取代原始来源。

## 6. Dexter 参考边界

实现或扩展交互 Agent 时，先阅读用户指定的 Dexter：

- [Dexter GitHub](https://github.com/virattt/dexter)
- [主 Agent 循环](https://github.com/virattt/dexter/blob/main/src/agent/agent.ts)
- [工具注册表](https://github.com/virattt/dexter/blob/main/src/tools/registry.ts)
- [Skills 注册表](https://github.com/virattt/dexter/blob/main/src/skills/registry.ts)
- [Scratchpad](https://github.com/virattt/dexter/blob/main/src/agent/scratchpad.ts)

可借鉴：有限 Agent 循环、工具描述、调用预算、循环检测、Skill 元数据、上下文压缩和执行轨迹。

不能直接复制：Bun/TypeScript/Ink 技术栈、Dexter 的开放网络权限、股票筛选、DCF/目标价、自动交易相关行为，以及把临时回答直接当成正式研究报告的做法。

## 7. 进度事件和当前 Agent 数量

`collect`、`write-draft`、`ask` 和 `chat` 支持简短 `ProgressEvent`。默认输出到 stderr，stdout 仍保留最终 JSON；不输出隐藏推理、密钥、完整原文或完整工具结果。

`memory-search TICKER [--query TEXT]` 列出可检查的同公司记忆索引，`memory-read MEMORY_ID` 读取一条已保存的记忆，`ask-status SESSION_ID` 同时显示 Scratchpad、用过的记忆和分析执行审计。

当前只有一个完整交互 Agent。自然语言 Router 是一次结构化分类调用，正式报告 Writer 是一次结构化写作调用，都不是独立的多轮 Agent。

## 8. 实现组织

交互 Agent 按职责拆分为小模块：`agent.py` 保留公开入口与授权快照；`agent_session.py` 管理生命周期和事务；`agent_execution.py` 提供共用预算、工具执行与答案接收；旧 JSON 循环放在 `legacy_agent.py`，Pi 事件适配放在 `pi_agent.py` / `pi_attempt.py`。`agent_policy.py` 动态构造 system prompt；`tool_contracts.py` / `tool_registry.py` 绑定参数与普通 handler；`agent_results.py` 统一内部结果和有界投影；`agent_memory.py` 管 Scratchpad/可信记忆，`agent_errors.py` 统一受控错误。2026-10-10 已完成的边界整理见 [执行架构说明](35-agent-execution-architecture.md)。

system prompt 不是独立静态 Markdown：Tool schema 与 Skill 方法正文必须按本轮授权动态组装。不可变限制（不修改正式研究、不可 finish、不可编造引用、JSON 决策协议）放在 Skill 之前；Skill 只能补充分析方法，不能授予权限。实际安全控制仍在 Tool Registry、handler 和最终引用校验中。详见 [交互 Agent 的内部组织](20-agent-runtime-organization.md)。

## 9. 后续扩展

反方可以继续作为当前 Agent 的开放问题；宏观、同行、产能和库存周期专用 Agent 本阶段暂不新增。阶段 19C 已把既有 `AnalysisWorkspace` 的新版 Skill 路径演进为 QueryWorkspace：每次 `ask`/`ask-followup` 都创建最小的 `AgentTurn`，使快照、SQL/函数、工具证据和配额归属一个用户问题，而不是整段 session。可信计算优先；不足时才在当前 session/turn/ticker/run 的隔离只读财务事实快照上执行受限 SQL，最后才对 SQL 结果调用受限 `analyze(rows)` 函数。它不读取来源全文、不直接查询项目主库、不改固定研究，并以实际工具回执而非模型声明验证最终引用。SQL/函数失败会作为可恢复工具错误返回模型。细节见 [Turn 级 QueryWorkspace](21-query-workspace.md)。

阶段 19D 随后补齐最小运行时语义：为 Turn 加入 Attempt、合法状态迁移、非覆盖式临时报告、带目标 Turn ID 的等待用户恢复/失败重试，并把事件和 trace 关联到实际执行；重试不会恢复 Python 调用栈或刷新 Turn 预算。复用已有 Tool Registry 与 Scratchpad，不复制 Dayu 的完整 Host/EventLog/队列。19D 的后半还会增加离线演示、英文 Quick Start、CI 与面试案例。阶段 19E 才在这些稳定边界上建立开放问题 Agent 的跨场景评测基线；19C/19D 的安全和 fake-model 验收测试不延后，也不单列记忆专项评测。Pi Agent Core 仍只在阶段 21 的公开基准证明需要上下文、steering、流式事件或多模型运行时收益时评估。

LangGraph 不是当前依赖。只有多轮恢复、跨天状态、并行任务或审计复杂度超过现有 Python 循环时才评估；即使引入，也只能调用现有 Python 服务，不能直接改评分或执行 `finish`。


2026-10-07 更新：19D.1 最小运行时与可靠性收口已实现，包含同 Turn respond/retry、不可变报告、事务、预算预留、执行锁和遗留中断结算。详见 [执行与恢复](23-agent-runtime-reliability.md)。19D.2 仍待实施，GitHub Actions 按最新用户要求暂缓；上文阶段路线保留为设计背景。

2026-10-08：用户明确要求参考 Pi 核心设计，本次在现有 Python 循环内落实 `transform_context/convert_to_llm` 和工具完整结果/模型投影边界，没有引入 Pi runtime。阶段 21 的门槛仍用于是否迁移框架，不妨碍借鉴设计。
