# 阶段 17：Skill 按需加载与 Capability Registry

## 1. 目的

Skill 用于补充研究方法和执行上下文，不能用 Markdown 取得程序权限。阶段 17 把启动时的“全文加载”改为两层流程：

```text
扫描摘要 → Router 选择名称 → Python 校验声明
                         ↓
               用户确认后的执行边界
                         ↓
          加载 SKILL.md 正文和声明的参考资料
```

Router 从不接收 Skill 正文或 reference 内容。固定研究和交互研究在真正执行时才读选中的内容。

## 2. 两类 Skill

### 方法 Skill

方法 Skill 只说明分析方法、输出约束和可选参考资料。只要新建下列文件，就会被发现：

```text
skills/<name>/SKILL.md
```

如果没有 `skill.json`，它默认是 `kind=method`、版本 `1`、不声明任何 capability。被选择后，固定 Writer 或交互 Agent 会把它作为补充方法上下文；Python 的来源、时间、评分、schema 和定稿规则优先且不可由它修改。

### 能力 Skill

能力 Skill 必须有 `skill.json`，并且只能引用 Python 已注册的 capability。例如：

```json
{
  "schema_version": 1,
  "kind": "capability",
  "version": "1",
  "applies_to": ["fixed_research"],
  "dependencies": ["company-analysis-method"],
  "capabilities": ["fixed_research"],
  "prompt_resources": ["references/method.md"]
}
```

当前 registry 位于 `src/wonder_stockresearch/capabilities.py`：

| capability | 状态 | Python 控制的边界 |
| --- | --- | --- |
| `fixed_research` | 可用 | 创建 ResearchRun、只读采集、写草稿；仍要 validate 和用户确认 finish |
| `interactive_research` | 可用 | 读取已归档资料、创建 Agent session 和临时报告 |
| `analysis_workspace` | 可用 | 从已归档资料导出最小只读数据集，并在受限 Python AST 环境执行临时代码 |

`capabilities.py` 为每一项定义 Pydantic 输入/输出模型、权限说明和审计目标。Skill 不能通过 `SKILL.md` 增加网络、数据库写入、shell 或代码执行权限。

## 3. Catalog 与加载契约

`skill_registry.catalog()` 生成每个 Skill 的摘要：名称、description、相对路径、类别、版本、适用 capability、可用性和内容哈希。Router Prompt 只收到其中的：

```json
{
  "name": "company-research",
  "description": "…",
  "path": "skills/company-research/SKILL.md",
  "kind": "capability",
  "applies_to": ["fixed_research"],
  "available": true
}
```

`RoutePlan.skill_names` 是最多 8 个不重复名称的数组。旧 Router 输出没有该字段时兼容为空数组。Python 在 Router 返回后只按 Catalog 元数据检查名称、依赖、适用 capability 和注册 capability；这一步不会把正文或 reference 传给模型。

确认并进入执行后，`load_for_capability()` 才读取 `SKILL.md` 正文与 `prompt_resources`。资源必须是 Skill 目录内的已有相对文件。加载得到的内容会重新计算哈希；目录外路径、缺资源、重名、循环依赖、未知 capability、尚不可用 capability、重复选择和 capability 不匹配都会拒绝执行并给出诊断。

## 4. 现有 Skill 的映射

| Skill | 类型 | capability | 依赖 |
| --- | --- | --- | --- |
| `company-analysis-method` | 方法 | 无 | 无 |
| `company-research` | 能力 | `fixed_research` | `company-analysis-method` |
| `interactive-research-agent` | 能力 | `interactive_research` | `company-analysis-method` |
| `analysis-workspace` | 能力 | `interactive_research`, `analysis_workspace` | `company-analysis-method` |

因此 Router 对“写正式报告”可以选择 `company-research`，对“问一个开放问题”可以选择 `interactive-research-agent`。依赖会先加载，最终审计顺序为方法 Skill、能力 Skill。

## 5. 执行与可追溯性

`start TICKER --skill company-research` 在创建 run 前加载并校验 Skill。实际加载的名称、版本、内容哈希、类别和 capability 写入 `run_steps` 的 `skill_selection` 元数据，不保存正文或参考资料。

随后执行 `write-draft` 时，Writer 从该审计记录按需重新加载内容，将方法上下文加入其受控 Prompt，并把同一份摘要写入 `write_draft` 元数据和输入哈希。若正文或 reference 已改变，Writer 返回 `skill_changed_since_selection`；应创建新 run，而不是混用两套方法。

`chat` 的交互分析在用户确认后加载 Skill。正文和 reference 只在该次 Agent 执行内进入上下文；Agent event 只保存摘要。`ask-followup` 会用 event 的名称重新加载并核对哈希，发现变化时停止会话并要求重新发起。

阶段 19B 增加可选 manifest 字段 `tools`。开放问题执行会把 capability 的基础只读工具与所选 Skill 声明的注册工具合并，模型只收到该集合的 schema，Python dispatch 使用同一集合做执行前校验。授权工具 ID、Skill 版本/哈希和 registry 版本进入 session event；follow-up 若发现 Skill 或授权变化会停止，不静默扩大权限。`analysis-workspace` v3 当前授权财务归档查询、可信计算、QueryWorkspace schema/SQL/函数/产物读取；旧 JSON 数据集工具只保留在兼容入口。基础 `interactive-research-agent` Skill 不需要修改即可保持原有说明。

## 6. CLI

```bash
wonder-research --root <项目根> skill-list
wonder-research --root <项目根> skill-check
wonder-research --root <项目根> skill-check company-research
wonder-research --root <项目根> start HK.00941 --skill company-research
```

- `skill-list` 返回 Catalog、哈希和可用性。
- `skill-check` 检查 manifest、资源路径、依赖和 capability；任一诊断使它返回 exit 2。
- `start --skill` 可重复使用；同一名称重复指定会拒绝。未指定时保持既有固定研究流程。

所有业务结果仍是 stdout JSON，长任务进度仍写 stderr。Skill 正文、reference、隐藏推理和 API Key 不进入 SQLite 审计记录。

## 7. 当前边界与下一步

开放问题 Agent 已能按问题动态选择工具顺序；阶段 19B 现在把所选 Skill 的 `tools` 声明接入 Python Tool Registry，并向模型只暴露合并后的 allowlist。新增 Skill 可以组合已有函数而不修改 Agent loop；新增函数须先在 Python 注册参数 schema 和 capability，再由 Skill 引用。直接 `ask` 入口为兼容旧用法仍保留现有工具集合；自然语言 `chat` 才按 Router 选中的 Skill 收窄或扩充本次工具集合。

这一扩展只用于开放问题 Agent 的动态工具组合。固定研究仍按已验收流程走 EvidencePack、六组 Writer、结构/语义审查和 `finish` 门闸。任何 Skill 都不能通过 Markdown 执行任意代码、shell、网络访问或修改研究规则。具体设计和验收见 [阶段 19B：Skill 驱动的受控工具路由](17-skill-driven-tool-routing.md)。
