# 阶段 19B：Skill 驱动的受控工具路由

## 目标

让开放问题 Agent 能根据问题选择一个或多个 Skill，并在当前问题中灵活组合这些 Skill 明确授权的 Python 函数。新增 Skill 如果复用已有函数，只需新增 `SKILL.md` 和 manifest；Agent 不再把所有工具无差别放进每个问题的上下文。

这不是把程序权限交给 Skill，也不是让 Agent 任意生成 Python、shell 或网络调用。新增一种真实操作时，仍须先由项目在 Python 中实现并注册经过验证的 handler。

## 当前实现（阶段 19B）

- 新增 `tool_registry.py`，为现有 Agent 工具集中保存稳定 ID、描述、Pydantic 参数 schema、适用 capability 和默认可见状态；工具实际 handler 仍由 Python Agent 侧受控 dispatch 执行。
- `skill.json` 可选声明 `tools`。Catalog 会拒绝未知工具、重复 ID、方法 Skill 声明工具和 capability 不兼容的授权。
- `chat` 在确认后按所选 Skill 计算基础工具与额外 grants，只把该集合的 JSON Schema 放入 Agent 上下文；执行时对每个模型工具调用用同一 allowlist 再检查。
- 当前七个来源/报告/Memo 工具属于交互 capability 的基础集合；财务查询、可信计算以及 19C 的 QueryWorkspace 工具由 `analysis-workspace` Skill 授权。直接 `ask` 为兼容旧调用暂时保留原有完整工具集合。
- session event 保存 Skill audit、allowlist 和 registry version；follow-up 重载 Skill 并验证 hash、授权集合和注册表版本。旧 session 使用旧工具集合兼容路径。
- `interactive-research-agent` 的 manifest 未改；`analysis-workspace` 可用于 `interactive_research`。19B 当时的五个旧工具授权已在 19C 升级为 v3 的六个工具授权，其中包含 QueryWorkspace；这一变化仍经过相同的 manifest、Registry 和 handler 三重检查。

离线验证覆盖新 Skill 授权、未知/不兼容工具拒绝、prompt schema 和执行 allowlist 一致，以及 follow-up 检出授权变化。当前没有发起真实模型请求；Agent loop 的调用顺序仍由模型在已授权工具集合内选择。

## 改造前的行为（阶段 19B 前）

```text
自然语言 Router
  → 选择 Skill 名称
  → Python 校验 Skill/capability
  → 执行时加载 Skill 正文和 references
  → 开放问题 Agent 从 agent.py 的全局静态工具列表中选工具
```

改造前已经有两种“动态”：

- Router 在运行时选择合适的 Skill，Skill 正文按需进入上下文。
- Agent loop 在每一轮运行时选择工具，并决定调用顺序。

但当时工具候选集合并不动态：Skill 不会给 Agent 增加或缩小可用函数。阶段 19B 已把所选 Skill 声明接入 Tool Registry，让开放问题 Agent 只拿到当前授权集合。固定研究的 Q01–Q24、六组写作、审查和定稿步骤则是刻意固定的质量流程。

## 当前开放问题调用路径

```text
用户问题
  → Router 选 Skill
  → Python 解析 Skill 依赖和 manifest
  → capability + Skill 声明合并为本次 tool allowlist
  → Agent 只收到 allowlist 内的 JSON Schema
  → Agent 自行选择本次工具顺序和重复/停止时机
  → Python 校验参数、执行注册 handler、保存审计
```

固定研究保持现有路径，不经由这个开放式工具循环：

```text
EvidencePack → 六组 Answers → Python 校验/组装 → Review → finish 门闸
```

## Python Tool Registry

每个工具定义由 Python 持有，至少包含：

- 稳定 `tool_id` 和面向模型的简短说明。
- Pydantic 参数模型；handler 返回 JSON 兼容的结构化结果。
- Python handler，不接受 Skill 提供的函数路径或可执行代码。
- 允许的 capability 和默认是否可见；细粒度权限继续由 Python capability 和受限服务执行。
- 调用记录通过现有 `agent_events` 和 trace 维护；工具错误使用受控错误码。

注册表是唯一可执行函数来源。未注册 ID、当前 capability 禁止的 ID、当前 Skill 未授权的 ID，都必须在 handler 调用前拒绝。模型看到的工具 schema 与 Python 实际执行授权使用同一份解析结果，避免“提示词隐藏但执行仍可调用”。

现有 Agent 工具已接入 registry；既有数据访问和计算逻辑仍由 Agent 的 Python dispatch 执行。添加新操作时要同时注册参数 schema、capability 和 Python handler，再由 Skill manifest 引用；只新增 `SKILL.md` 无法创建新的 Python 函数。修改 handler 的权限或行为时应更新 `tool_registry.REGISTRY_VERSION`，让活动 session 在 follow-up 时要求重新发起。

## Skill manifest 扩展

兼容当前 `skill.json`，增加可选字段：

```json
{
  "schema_version": 1,
  "kind": "capability",
  "version": "1",
  "applies_to": ["interactive_research"],
  "dependencies": ["company-analysis-method"],
  "capabilities": ["interactive_research"],
  "tools": ["query_archived_financials", "run_trusted_calculation"]
}
```

约束：

- `tools` 只能包含 registry 中已存在的 ID，且该工具须允许 manifest 声明的 capability。
- Skill 依赖树的授权集合按确定顺序合并并去重；审计保存最终集合和每个工具的授权来源。
- 未知工具、重复项、能力不匹配或 manifest 非法时 Skill 标记不可用并给出诊断。
- 纯方法 Skill 可不声明 `tools`，只提供方法上下文。
- 可执行 Skill 不能用自然语言或 references 扩大工具权限。

建议为交互 Agent 保留小而必要的基础工具集合（例如来源目录、已归档来源搜索和读取）；研究专项 Skill 再声明财务查询、可信计算、临时数据集/代码执行等工具。最终清单应从现有工具和真实问题基准中确定，不能把“通用”理解成每个任务都拿到全部工具。

## Agent 接口和审计

- `ask`/`ask-followup` 会在创建或恢复 session 时确定工具授权；follow-up 必须从会话保存的 Skill 选择及内容哈希重新解析，授权变化时明确停止或启动新 session，不能静默扩大权限。
- system prompt 和模型工具 schema 仅包含最终 allowlist；模型尝试调用其他 ID 返回受控 `unauthorized_tool`，不执行任何副作用。
- 现有 `agent_events` 记录工具调用结果，schema v9 trace 保存工具 span。增加 Skill/tool 授权摘要、tool ID 和 registry 版本；不保存完整 prompt、隐藏推理、密钥或大段工具结果。
- 工具顺序由当前 Agent loop 决定；预算、参数 schema、重复调用检测、来源引用校验和 `AnalysisWorkspace` 约束继续生效。

## 阶段拆分记录

1. **Registry 最小化**：定义 `ToolDefinition`、按 capability 解析 allowlist、把现有工具目录与 dispatch 接到 registry；行为保持兼容。
2. **Skill 授权**：支持 manifest `tools`，验证 ID 和 capability，加载依赖后生成本次可用工具集合。
3. **Agent 上下文隔离**：仅把 allowlist 的 schema 发送给模型，执行时重复校验，不可信名称不可绕过。
4. **会话恢复和审计**：将 Skill 选择、内容哈希、registry 版本和授权工具集合固化到 session/event/trace，定义 follow-up 变更行为。
5. **真实 Skill 示例**：交互研究 Skill 使用来源基础工具；`analysis-workspace` 组合财务检索、可信计算与 QueryWorkspace；再增加一个不带新 Python 代码、只重组现有工具的方法 Skill，验证可扩展性。

每个子项完成时按项目教学约定执行有意义的离线验证，更新中文说明和 `PROGRESS.md`。不调用交易/账户接口，不写正式报告，不自动 `finish`。

## 验收条件

- 新建 Skill 并引用现有 tool ID 后可被发现、选择并向 Agent 暴露对应 schema，不需改 loop 代码。
- 未选工具不出现在当前 Agent 的工具 schema 中；直接伪造调用也会在 handler 前被拒绝。
- 未知 ID、capability 不匹配、非法依赖和 Skill 内容变化都有明确诊断。
- 多 Skill 的允许工具集合稳定、可审计；会话恢复不会暗中增加权限。
- 现有开放问题功能和 `AnalysisWorkspace` 行为保持正确，工具预算、来源定位和报告隔离不回退。
- Fixed Research 仍使用确定性流程，Skill 的工具声明不能改变其写作分组、校验、评分或 finish 门闸。

## 后续边界

完成本阶段后再做离线 demo 与面向新用户的上手改造。多 Agent、Pi Agent Core、LangGraph 或通用 shell 均不属于本阶段。是否采用 Pi 或并行 worker，等同题基准提供证据后再决定。
