# 阶段 19A：调用轨迹、Phoenix 与 Langfuse

本项目已经能在不改动 DeepSeek 调用方式的前提下，记录从一次模型请求到 Agent
完成的调用轨迹。默认用于排查“模型调用了几轮、用了什么工具、在哪一段失败、用了多少
token”；在用户显式开启本机 Phoenix 的内容采集后，也可查看可见的模型上下文与回答。

## 默认的本地轨迹

每次 `write-draft`、`review-draft`、`ask`、`ask-followup` 和 `chat` 的 Router 调用会
写入项目 SQLite 的 `traces` / `trace_spans`。Writer 的轨迹通过 `RUN_ID` 查询；交互
Agent 的轨迹通过 `SESSION_ID` 查询：

```bash
uv run wonder-research --root . trace-read RUN_ID
uv run wonder-research --root . agent-trace SESSION_ID
```

两个命令都只向 stdout 输出 JSON，长任务进度仍只写 stderr。顶层 trace 带有
run/session 关联，内部 span 使用 `llm`、`tool` 或 `chain` 种类；典型 Agent trace 是：

```text
agent.ask
├── skills.selected
├── agent.iteration.1
│   ├── deepseek.chat.completions (agent_decision)
│   └── agent.decision → tool_call(read_source)
├── tool.read_source
└── agent.iteration.2
    ├── deepseek.chat.completions (agent_decision)
    └── agent.decision → final
└── … → completed / failed / waiting_user
```

默认记录的字段包括名称、状态、开始/结束时间、耗时、provider/model、调用种类、
token 数、输入条数/长度/哈希，以及工具参数和紧凑结果的长度/哈希。导出的 LLM span 使用
OpenInference 的 `llm.model_name`、`llm.token_count.*` 和 `openinference.span.kind`，供 Phoenix
识别与筛选。完整 prompt、用户
问题、模型回答、完整工具结果、隐藏推理、API key、Authorization 和异常原文不会进入
本地 trace。`agent_events` 仍是业务层有限审计，不能替代 `agent-trace` 的时间树。

固定 `write-draft` 是 Python 预先构造 EvidencePack 后的受控写作流程，因此会显示多个 LLM
span，却不会有 Agent `tool.*` span；只有 `ask` / `ask-followup` 中模型实际选择了只读工具时，
才会显示工具节点。Agent 当前通过受控 JSON 决策协议选择工具，而非 DeepSeek 原生 `tools` 参数；
所以 Phoenix 的真实层级是 `agent.decision → tool.read_source`，这同样能完整还原项目实际执行的
tool use。

`trace-read` 只会显示曾调用 Writer/Reviewer 的 run；尚未调用模型的 run 返回空的
`traces` 数组。Router 是一个独立的自然语言分类 trace；它不会伪造为后续 research run
或 Agent session 的父节点。

## 在本机 Phoenix 查看同一套 span

本地 SQLite 轨迹不依赖额外包。若要在浏览器中按时间轴、模型、token 和工具查看，项目
可以把相同的安全元数据以标准 OpenTelemetry OTLP/HTTP 导出到 Phoenix。先安装项目的
可选 exporter 依赖，并在另一个终端启动本地 Phoenix：

```bash
uv sync --extra observability
uvx --from arize-phoenix phoenix serve
```

Phoenix 启动后通常在 <http://localhost:6006> 提供 UI。保持它运行，在执行项目命令的
终端设置：

```bash
export WONDER_TRACE_EXPORTER=otlp
export WONDER_OTLP_ENDPOINT=http://127.0.0.1:6006/v1/traces
export WONDER_TRACE_PROJECT=wonder-stockresearch

uv run --extra observability wonder-research --root . ask HK.00728 --input question.json
# 或：uv run --extra observability wonder-research --root . write-draft RUN_ID
```

完成命令后刷新 Phoenix 的 Traces 页面即可看到新 trace。`WONDER_OTLP_ENDPOINT` 可指向
任何兼容 OTLP/HTTP trace 接收器；因此以后迁移到 Langfuse 或受控 collector 时，不需要
改 Writer、Agent 或数据库模型。默认导出层不会发送 input/output 正文，也不会把异常原文发送给
后端。`WONDER_TRACE_PROJECT` 同时写为标准 `service.name` 与 Phoenix/OpenInference
的 `openinference.project.name`，因此 Phoenix 会将新 span 收入同名项目。

`WONDER_TRACE_EXPORTER` 未设置时只保留 SQLite 轨迹；设置为 `otlp` 但未安装 optional
依赖或 Phoenix 不可用时，研究/Agent 的业务结果不受影响，仍可使用上述两个本地读取命令。

## 用 Langfuse 看 Agent Graph（可选）

Langfuse 是远程或自托管的 OTLP 接收端。项目不依赖 Langfuse SDK：设置
`WONDER_TRACE_EXPORTER=langfuse` 后，既有 OpenTelemetry span 会带上 Langfuse v4 所需的
Basic Auth、实时导入头和 observation type 映射。LLM、Tool、Agent 和 Chain 会分别显示为
`generation`、`tool`、`agent`、`chain`；一个开放问题 session 的 `ask` / `ask-followup` 会按
`session_id` 自动分组。因此无需改 Writer、Agent 或 DeepSeek 请求。

1. 在 Langfuse 创建项目，在 **Settings → API Keys** 创建一对项目 key。
2. 将 key 放进项目根目录的 `.env`（它已被 Git 忽略），不要把 key 放进命令历史或提交：

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
# EU Cloud 默认就是下列地址；US Cloud 改成 https://us.cloud.langfuse.com。
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

3. 安装已有的 exporter extra，并在同一个终端运行研究：

```bash
uv sync --extra observability
export WONDER_TRACE_EXPORTER=langfuse
export WONDER_TRACE_CAPTURE_CONTENT=preview  # 可选；只在接受把本次可见 I/O 发往 Langfuse 时开启

uv run --extra observability wonder-research --root . \
  chat --text "分析腾讯未来两年的主要风险"
```

EU、US、日本或自托管实例都可以通过 `LANGFUSE_BASE_URL` 指向对应根 URL；如有反向代理或非标准
路径，也可以直接设置完整的 `WONDER_LANGFUSE_OTLP_ENDPOINT`（必须以
`/api/public/otel/v1/traces` 结尾）。项目自动构造 `Authorization: Basic …`，不会打印、写入
SQLite 或保存在 trace 属性里。

在 Langfuse 的 **Traces** 打开新 trace 后选择 **Agent Graph**：

- **Aggregated** 适合讲解架构：重复的工具/LLM 节点会合并并显示调用次数；
- **Expanded** 适合复盘一次运行：每轮模型决定和每次工具调用都会单独展开；
- 打开 session 即可把同一开放问题的后续追问串起来。

默认 `WONDER_TRACE_CAPTURE_CONTENT=off`：Langfuse 只收到安全元数据、时间、token 与结构，仍能
画图。`preview` / `full` 的隐私规则与 Phoenix 相同：可见 prompt、回答和工具 I/O 会被发送到你配置的
Langfuse 实例，但不会保存到本项目 SQLite；key、Authorization 和隐藏推理不采集。完成调试后执行：

```bash
unset WONDER_TRACE_EXPORTER WONDER_TRACE_CAPTURE_CONTENT
```

参考：Langfuse 的 [OTLP 接入与 v4 实时导入头](https://langfuse.com/integrations/native/opentelemetry)、
[Agent Graph 说明](https://langfuse.com/docs/observability/features/agent-graphs)。

## 在 Phoenix 看模型回答与 Agent tool use（显式本机调试）

“正文脱敏”指默认不将 prompt、模型可见回答、工具入参或工具结果写入 trace。它不等于模型
没有回答；只是默认不把这些内容持久化到观测系统。若当前 Phoenix 是你的本机调试实例，并且
你接受其中保存本次请求携带的研究材料，可在**执行命令的同一个终端**额外设置：

```bash
# preview：向 Phoenix 导出每段最多 12,000 字符的可见 I/O；SQLite 不保存正文。
export WONDER_TRACE_CAPTURE_CONTENT=preview

# 可选：调整 preview 上限。
export WONDER_TRACE_PREVIEW_CHARS=12000
```

此时 Phoenix 的 `deepseek.chat.completions` span 会显示 OpenInference 的
`llm.input_messages.*.message.content` 与 `llm.output_messages.*.message.content`；`tool.*`
span 会显示 `input.value`（工具参数）和 `output.value`（工具结果）。这正是检查“模型收到什么、
回答什么、随后如何调用只读工具”的完整可见链路。

若确实需要不截断的内容，可改为 `WONDER_TRACE_CAPTURE_CONTENT=full`。这会把完整可见模型消息和
完整工具 I/O 发给你配置的 OTLP collector，适合只在本机、非敏感的调试 run 使用；完成后运行
`unset WONDER_TRACE_CAPTURE_CONTENT` 恢复默认关闭。无论模式为何：项目 SQLite、`trace-read` 和
`agent-trace` 都不会保存这些正文；API key、Authorization/认证头、以及模型的隐藏推理字段
（例如 `reasoning_content`）不会被采集，异常原文也不会被导出。

参考：Phoenix 的[本地部署说明](https://arize.com/docs/phoenix/self-hosting/deployment-options)、
[OTel 接入说明](https://arize.com/docs/phoenix/sdk-api-reference)、[OpenInference 语义约定](https://arize-ai.github.io/openinference/spec/semantic_conventions.html)，以及
[OpenTelemetry Python OTLP exporter](https://opentelemetry.io/docs/languages/python/exporters/)。
