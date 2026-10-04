# 阶段 8–10：独立交互 Agent、临时报告与公司 Memo

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

用户明确选择保存的公司长期观察，保存在 `company_memos`：

- 由 `memo-save` 显式创建。
- 必须带至少一个 `source_id + locator`。
- 同标题再次保存会生成新版本，不静默覆盖旧 Memo。
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

当前 `allow_network=true` 会明确返回不支持。需要联网采集时先走固定的 `collect` 或 `source-add`，之后再用 `ask` 读取归档材料。

`focus` 可以选择 `general`、`challenge`、`macro`、`peer`、`capacity_cycle` 或 `inventory_cycle`。这些是同一个 Agent 的受控分析角色，不是相互独立的长期 Agent；它们共享相同的只读工具、证据校验和临时报告边界。

当前自然语言入口：

```bash
wonder-research --root <项目根> chat
wonder-research --root <项目根> chat --text "帮我研究一下中国移动"
```

非严格自然语言也可以触发候选正式研究，但必须先确认。正式研究自动执行到 `validate`，通过后还要再次确认才会 `finish`。自然语言 Router 不直接调用数据库、富途或文件系统。

## 4. Agent 工具边界

当前 Agent 只注册只读工具：

- `get_run_status`
- `list_sources`
- `read_source`
- `search_archived_sources`
- `read_report`
- `list_memos`
- `read_memo`

每个工具有 Pydantic 参数模型。Agent 不能调用任意 Python、SQL、shell、网络或交易接口。工具结果被截断后才重新放入上下文，调用事件保存在 `agent_events`，不保存隐藏推理、完整认证头或 API Key。

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

正式研究使用 `runs`；Agent 不把临时会话伪装成 `runs`。重启后可以依靠 session ID、SQLite 和 `data/` 恢复状态，不依赖 OpenCode 私有会话数据库。

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

当前只有一个完整交互 Agent。自然语言 Router 是一次结构化分类调用，正式报告 Writer 是一次结构化写作调用，都不是独立的多轮 Agent。

## 8. 后续扩展

反方可以继续作为当前 Agent 的开放问题；宏观、同行、产能和库存周期专用 Agent 本阶段暂不新增。下一项专业能力优先考虑同行业竞争 Agent，但需要用户先明确研究方法、比较对象和数据范围。只有真实运行证明需要独立上下文、并行执行或独立质量评估时，才拆分子 Agent。

LangGraph 不是当前依赖。只有多轮恢复、跨天状态、并行任务或审计复杂度超过现有 Python 循环时才评估；即使引入，也只能调用现有 Python 服务，不能直接改评分或执行 `finish`。
