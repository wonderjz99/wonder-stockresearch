# 交互 Agent 的内部组织

本章只整理既有交互 Agent 的代码边界，不增加 Tool、数据源、模型供应商、数据库表或 CLI 命令。`ask`、`ask-followup`、Memo、trace、Tool Registry、Skill 授权、Scratchpad 和正式研究隔离的外部行为保持不变。

## 1. 为什么要拆开

交互 Agent 既要接收模型决策，又要检查工具权限、保存会话、压缩上下文和校验证据。把这些内容持续堆进一个文件，会使新增 QueryWorkspace 或后续 Turn/Attempt 时难以确认某项变更是否触及研究边界。

当前结构按运行时职责分层：

```text
agent.py（会话入口与有限循环）
  ├─ agent_policy.py（不可变策略 + 动态 system prompt）
  ├─ agent_tools.py（Tool handler 与归属校验）
  ├─ agent_memory.py（Scratchpad、压缩、可信长期记忆）
  ├─ agent_errors.py（受控错误形状）
  └─ tool_registry.py（可见 Tool schema、capability 与 allowlist）
```

`agent.py` 仍是 `ask`、`ask-followup`、`memo-save` 等公开 Python 入口所在处，也是唯一驱动“模型决策 → Tool → 下一轮 → 临时报告”的有限循环的位置。它不再保存大段 prompt、记忆投影或每个 Tool 的业务分支。

## 2. System prompt 为什么是 Python 文件

`agent_policy.py` 不是单一静态 `system_prompt.md`。它有两类输入必须在每轮根据受信任的运行时数据生成：

1. Tool Registry 解析出的本轮 JSON schema；
2. 已校验、按需加载的 Skill 方法正文。

因此 `build_system_prompt()` 按下列顺序组装：

```text
不可变权限与证据规则
→ 数值计算优先规则
→ JSON 决策协议
→ 当前 focus 指引
→ 已授权 Tool schema
→ 可选 Skill 方法内容
```

Skill 永远放在最后，且 prompt 明确说明它不能改变前面的权限、Tool allowlist、引用规则或 JSON 协议。更重要的是，Python 的 Tool 执行与最终引用校验仍会独立执行这些限制；prompt 不是安全边界。

`FOCUS_INSTRUCTIONS` 目前仍是少量内置分析视角。以后如需增加同行、反方或周期研究的方法，可将方法性内容做成 Skill；“不交易、不 finish、不能伪造引用”等规则仍留在 `agent_policy.py` 和 Python 校验中。

## 3. Tool、Skill 与 Policy 的边界

- `tool_registry.py`：声明稳定 Tool ID、Pydantic 参数 schema、capability 与默认可见性。
- `agent_tools.py`：在执行前解析参数，并检查 ticker、run、来源、报告和 Memo 的实际归属；它调用 `research.py`、`storage.py` 或 `analysis_workspace.py`，不会向模型开放任意文件、SQL、网络或 shell。
- `skills/**/SKILL.md`：只给模型补充研究方法；`skill.json` 可以申请已注册 Tool，但不能创建 Tool、绑定 handler 或扩大 capability。
- `agent_policy.py`：定义模型必须遵守的交互协议；它可见但不承担授权职责。

## 4. 从 Pi Agent Core 借鉴了什么

Pi Agent Core 将状态化循环、Tool 执行、上下文变换和事件流分开。本项目借鉴其中两项小而明确的想法：

1. 上下文压缩是一个独立步骤：`agent_memory.compress_messages()` 在发送模型前，以可检查的 Scratchpad 替换旧 Tool 正文；完整事件仍留在 SQLite。
2. Tool 的可见 schema 与可执行 handler 分离：Registry 决定本轮模型看见什么，handler 在 Python 中再次检查授权与归属。

本项目没有引入 Pi 的 TypeScript runtime、流式 UI、并行 Tool batch、steering queue、扩展系统或多模型路由。公司证据、评分、`ResearchRun`、`validate` 和 `finish` 仍是本地 Python 服务的权威职责。

## 5. 验证与下一步

现有 fake-model 回归继续覆盖 Tool 调用、Scratchpad 压缩、Skill allowlist、会话恢复、临时报告、Memo 和 trace。新增断言确认不可变策略位于 Skill 正文之前，Skill 不能在 prompt 结构上取代权限说明。

阶段 19C 接下来仍先实现 `AgentTurn`、隔离只读事实快照、受限 SQL、临时函数和工具证据回执；阶段 19D 才考虑 Attempt、恢复语义和最小模型适配层。本章的拆分不改变这些阶段边界。
