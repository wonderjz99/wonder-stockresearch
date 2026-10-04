# 阶段 11–12：进度事件与独立自然语言入口

## 1. 目标

自然语言入口由本项目 Python 运行时直接提供，不要求 OpenCode 或其他外部宿主：

```text
用户自然语言
    ↓
Skill Registry + DeepSeek Router
    ↓
固定 ResearchRun 或 Interactive Agent
    ↓
ProgressEvent + 最终结果
```

自然语言不是权限。Router 只能返回结构化 `RoutePlan`，真正的采集、写作、校验、评分和 `finish` 仍由 Python 服务完成。

## 2. 命令

连续对话：

```bash
wonder-research --root <项目根> chat
```

单次输入：

```bash
wonder-research --root <项目根> chat --text "帮我研究一下中国移动"
```

恢复 chat session：

```bash
wonder-research --root <项目根> chat --session-id <SESSION_ID>
```

`chat --text` 有两种行为：在真实终端中，它把这句话作为首轮输入，然后像 Dexter 式会话循环一样保留 session，继续接收“确认开始”“确认定稿”或追问；当 stdout 被管道/重定向时，它输出一次 `{ok, data, issues}` JSON，适合脚本。省略 `--text` 则直接进入同一个交互循环。两个入口使用相同的 Router 和 session 存储。

终端中的典型过程：

```text
$ wonder-research chat --text "帮我研究一下中国电信"
我理解为正式公司研究……是否开始？
wonder> 确认开始
……采集与校验……
研究草稿已通过校验，是否定稿？
wonder> 确认定稿
正式报告已归档并冻结
```

如果正式草稿没有通过来源或时间校验，TTY 会列出具体问题的位置、错误码、解释和 `run_id`，并说明正式报告尚未生成/冻结。该 run 保持 draft，可先检查来源或重新生成草稿；失败不会被当成成功。管道模式仍返回 `ok=false` JSON 和非零退出码。

简称消歧示例：

```text
$ wonder-research chat --text "帮我研究一下保利"
“保利”可能对应多个标的，请输入候选编号、完整公司名称或股票代码。
1. HK.06049 保利物业 [HK]
2. HK.00119 保利置业 [HK]
wonder> 1
已选择：保利物业（HK.06049）。确认开始正式公司研究吗？
```

标的搜索顺序是项目本地股票清单、富途 `get_search_quote`、DeepSeek 未验证候选。富途候选只保留港股/美股普通股，不读取或保存富途返回的 `is_watched`；模型候选会显式标记为“未验证”，不能绕过用户确认。

这里借鉴 Dexter 的会话式 Agent loop：用户输入、受控执行、状态反馈和下一轮输入属于同一个 session；本项目仍由 Python 服务控制确认、证据校验和报告状态，不复制 Dexter 的 TypeScript/Bun runtime。

## 3. 非严格自然语言与确认

支持以下类型的表达：

```text
写一下中国移动
帮我看看中国移动这家公司
研究一下移动
给我弄一份中国移动报告
```

如果 Router 判断为正式研究，会先返回候选计划：

```text
我理解为对中国移动（HK.00941）启动正式公司研究。
确认后将采集资料并生成草稿，是否开始？
```

用户确认后才执行：

```text
start
→ collect --kind all
→ write-draft
→ validate
→ 等待用户确认
→ finish
```

即使用户使用了明确的“写报告”表达，也不会自动绕过第一次确认。`validate` 通过后还需要明确确认 `finish`。

如果公司、股票代码或“正式报告/临时分析”含义不清，Router 返回 `clarify`，不创建正式报告。

## 4. RoutePlan

`RoutePlan` 是 DeepSeek Router 的唯一输出契约：

```json
{
  "intent": "fixed_research",
  "company_name": "中国移动",
  "ticker": "HK.00941",
  "question": null,
  "focus": "general",
  "confidence": 0.91,
  "reason": "用户要求生成公司研究报告",
  "requires_confirmation": true
}
```

支持的意图为：

```text
fixed_research
interactive_question
memo_save
session_followup
status_query
clarify
unsupported
```

Router 不能直接调用数据库、富途、文件系统或 shell。股票代码只能通过格式校验，最终由用户确认；模型不可靠时不能静默猜测。

## 5. Skill Registry

`skill_registry.py` 扫描项目根目录下的 `skills/*/SKILL.md`，读取：

- `name`
- `description`
- Skill 正文
- 相对文件路径

Skill 正文作为模型上下文，不是程序权限。程序仍然通过 Pydantic、工具白名单、研究服务和数据库状态控制实际操作。

Skill 的默认且完整执行路径是项目内 Python Skill Registry：

```text
独立 chat → Python Skill Registry → DeepSeek Router → 调用 Python 服务
```

本项目直接读取共享 Skill 文件并由 Python 服务执行。OpenCode 相关配置仅为历史集成记录，不作为产品功能、安装步骤或验收依赖。

## 6. ProgressEvent

长任务不再等到全部完成后才输出。`ProgressEvent` 的形状为：

```json
{
  "type": "progress",
  "phase": "collect.fundamentals",
  "status": "succeeded",
  "message": "基本面数据采集完成",
  "run_id": "...",
  "session_id": null,
  "timestamp": "..."
}
```

默认写入 stderr：

```text
[collect.fundamentals] 基本面数据采集完成
```

最终业务结果仍写入 stdout JSON；`--quiet` 可以关闭简短进度。

当前不输出：

- 模型隐藏推理；
- API Key 或认证头；
- 完整原始来源；
- 完整工具返回值。

未来 Web 或消息 Gateway 可以消费同一事件结构，不需要重写研究服务。

## 7. Agent 数量和范围

当前只有一个完整交互 Agent：

```text
Interactive Research Agent
```

自然语言 Router 是一次结构化分类调用，正式报告 Writer 是一次结构化写作调用，两者都不是独立的多轮 Agent。

本阶段暂不实现：

- 宏观 Agent；
- 产能周期 Agent；
- 库存周期 Agent；
- 同行业竞争 Agent。

同行业竞争 Agent 等用户明确研究方法、比较对象、期间和数据范围后再设计。

## 8. Runtime 决策

当前继续使用 Python。TypeScript 未来只能作为 UI、HTTP 或消息 Gateway，通过 JSON/HTTP 或 subprocess 调用 Python 服务；不能复制研究规则、评分、富途接口和 SQLite 业务逻辑。

LangGraph 也不是当前依赖。只有出现多轮动态补证据、复杂并行、跨天恢复或审计复杂度明显超过现有有限 Python 循环时才重新评估。
