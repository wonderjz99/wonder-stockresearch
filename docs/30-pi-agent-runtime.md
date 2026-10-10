# Pi SDK 问答运行时：实现与验收

2026-10-09，按用户确认的 Q1–Q8 实施。Python 领域核心继续管理证据、计算、报告和状态；新的开放问答路径使用项目自带 Pi。迁移尚处验收期，默认仍使用既有 Python loop；通过严格质量验收之后才删除旧 loop 并切换默认。真实题集的执行和研究质量分开记录，不能以 completed 代替正确。

## 复用边界

| 层 | 使用什么 | 原因 |
| --- | --- | --- |
| DeepSeek 请求、SSE、原生工具消息 | `@earendil-works/pi-ai` 0.83.0 | 复用 provider 协议与原生消息，停止自行拼接生产 tool_call JSON |
| Agent 循环、工具调用调度、事件 | `@earendil-works/pi-agent-core` 0.83.0 | 由 Pi 决定继续模型/工具循环；首版工具顺序执行 |
| 编码 Agent 产品 | 不接入 `pi-coding-agent` | 其文件/shell 工具、会话目录、CLI/UI 不属于股票研究权限范围 |
| 研究领域 | 本项目 Python | 工具授权、Pydantic 参数、公司范围、证据回执、受限 SQL/函数、累计预算、事务与冻结不能交给通用 SDK |
| 固定 Writer、自然语言 Router | 既有 Python | 单次受控写作/分类无需迁移为交互 Agent |

一个用户目标对应项目 AgentTurn；一次执行/重试对应 Attempt。Pi 的 turn 通常对应一次模型调用及工具批次，两者不可混为一谈。2026-10-10 整理后：`agent.py` 保留公开会话入口，`agent_session.py` / `agent_execution.py` 提供共用业务操作；`pi_agent.py` 准备运行，`PiAttempt` 处理原生事件，`pi_protocol.py` / `pi_bridge.py` 管版本 1 的严格 JSONL 通信。Node 的 `runner.mjs` 创建 Pi Agent，使用 beforeToolCall/afterToolCall/subscribe/transformContext，不读取研究文件或数据库。详见 [执行架构](35-agent-execution-architecture.md)。

## 安装与运行

需要 Python 项目环境和 Node >=22.19。运行时为仓库内独立依赖，用户提供的 pi-learning 仅作源码参考，不参与执行。首次安装或升级锁文件后：

```sh
cd src/wonder_stockresearch/pi_runtime
npm ci --omit=dev --ignore-scripts --no-audit --no-fund
```

仓库/安装包附带构建后的 `runner.mjs`，运行时不需要 TypeScript 编译器。开发修改 [src/runner.ts](../src/wonder_stockresearch/pi_runtime/src/runner.ts) 时使用不带 `--omit=dev` 的 npm ci，再执行 `npm run typecheck`、`npm run build`、`npm run check:generated`。SDK 类型检查和目录组织见 [执行架构](35-agent-execution-architecture.md)。

回到项目根目录，用环境变量显式选择尚在验收的 Pi 路径（不要把正文拼入 shell）：

```sh
export WONDER_AGENT_RUNTIME=pi
./.venv/bin/wonder-research --root . ask HK.00941 --input data/question.json
```

该配置选择新 ask、ask-followup 和 chat 交互问题的运行时；ask-respond / ask-retry 使用该 Turn 创建时固定的运行时，切换环境不改旧问题。ask-status、ask-interrupt 和 Memo 仍由 Python 处理。移除环境变量后新问题使用原默认。显式选择 Pi 却未安装 Node/依赖时返回 pi_runtime_unavailable，不暗中使用旧 loop。DEEPSEEK_API_KEY 仍由用户环境或项目 .env 加载，子进程仅继承环境，不经协议传输或落库。兼容 WONDER_DEEPSEEK_MODEL、WONDER_DEEPSEEK_BASE_URL；Pi 问答输出默认 4096，可通过问题的 output_token_limit 配置至 8192，始终受剩余累计预算约束。默认关闭 thinking、温度 0.2，独立于 Writer 的长报告参数。新问题默认 60,000/10 与单轮输入上限等可靠性修复见 [后续说明](32-pi-agent-reliability.md)。

## 消息、恢复与预算

- 主库 schema 13 新增 agent_messages：按 Turn 有序保存用户原话、可见助手文本、原生 toolCall ID/参数、结果摘要和产物 ID。隐藏 reasoning、签名、raw diagnostics、headers 和完整请求不入库。背景资料临时投影，不冒充用户原话。Pi 不创建另一套 session 文件。
- retry/respond 恢复同一 Turn 的规范化消息，只把完整 assistant toolCall + 对应 toolResult 组传回模型；半组留在审计记录中。成功工具按原有 Turn/参数哈希复用，函数/SQL 产物与回执仍由 Python 管理；补取后失效旧查询缓存。这里没有重新设计事件重放系统。
- 旧成功回答、正式报告、Memo 和来源不改写。旧 legacy 的 waiting/failed/interrupted Turn 仍以 legacy 恢复，即使环境已选择 Pi；新 follow-up 可创建新 Pi Turn。未知/不兼容的冻结运行时在创建 Attempt 前拒绝。此项替代首版仅在启用 Pi 时拒绝旧 Turn 的过渡规则。
- 每次模型请求前将剩余 Turn 额度作为最坏预留写库；收到确实存在的 provider usage 才结算。Pi 默认零用量不能作为免费请求证据。未知用量、硬崩溃保留预留；retry/respond 不刷新预算。输入规则/问题/原生 schemas 压缩后仍无法容纳时，请求之前停止，不额外收费。预算是保守估算与真实用量结算，不保证 provider 账单绝对上限。
- 原生工具的参数仍经项目 Pydantic 校验。可恢复的 SQL/函数错误返回 Pi，允许修正；未授权工具、身份错误、重复调用和累计预算仍由项目边界阻止。中断时清理 Node 进程组，已提交报告不受失败收尾影响。
- 最终 JSON 结构及来源/locator 回执校验仍不足以证明正文每个数值正确。单位、期间和计算依据需要独立验收；不因为接入 SDK 宣称模型更准确或更省 token。

## 验证方式与教学

离线注入器把既有脚本化决策转换为原生 Pi tool calls，实际执行 Pi loop；生产不使用该转换器。额外测试直接替换 Node fetch，驱动真实 pi-ai SSE/provider 与 pi-agent-core，检查原生调用 ID、工具 schema、usage 存在/缺失及隐藏推理过滤。运行：

```sh
WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q
./.venv/bin/ruff check src tests scripts
```

真实验证用 `scripts/eval_pi_agent.py`。首次只读备份原数据库与归档到 `data/audits/20261009-pi/isolated-root/`；原项目主库和报告哈希另存并在每题后检查。调用前持久化 ledger，崩溃/网络失败也计数；最多 40 次，默认跳过已执行题，指定 --cases 才做定向复测。Q29 依赖 Q11 的 session，以新 Turn 续问；Q25 保留 allow_network=false，模型 API 仍可使用。脚本输出执行状态，研究质量必须另行逐题核对；审阅结果见 [Pi 验收记录](31-pi-agent-evaluation.md)。

阅读顺序：从 agent.ask 看公开入口，再看 agent_session/agent_execution 的共用操作、PiAttempt 的事件适配，随后看 runner.ts 中的 Agent 与钩子，最后检查 pi_context 的完整消息组与最终事务。重点是“把通用模型/工具调度交给 Pi，把股票研究规则留在 Python”，而不是把研究系统搬进 coding-agent。当前保留旧 loop 是验收期间的临时安排，有阻塞项时不得删除可用默认入口。
