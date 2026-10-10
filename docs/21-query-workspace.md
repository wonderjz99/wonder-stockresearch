# 阶段 19C：Turn 级 QueryWorkspace

## 1. 这一步解决什么

阶段 18 的 `AnalysisWorkspace` 已能把少量归档数值导出为 JSON，并运行受限计算；但它的执行次数、产物与引用都只归属于整个 `AgentSession`。一次 follow-up 覆盖 session 的临时报告后，很难判断一条 SQL、一个预算和一条引用究竟属于哪个用户问题。

19C 增加最小的 `AgentTurn`，并将开放问题中的结构化财务分析改为 `QueryWorkspace`。它不是完整 Agent runtime：没有 `AgentAttempt`、重试命令、可恢复状态机或按 Turn 保存的报告历史；这些留给 19D。

固定研究的 `ResearchRun`、评分、门闸、Writer、`validate` 和 `finish` 没有任何新入口或写入路径。

## 2. 实际数据流

```text
ask / ask-followup
  -> AgentSession（长期对话容器）
  -> AgentTurn（本问题的输入、授权、预算快照）
  -> run_readonly_sql
       -> 从本 run 的归档数值构造最小 snapshot.db
       -> 独立受限 SQLite 子进程执行 SELECT
       -> analysis_runs + 文件审计产物
       -> fact_id 反查 source_id + locator
  -> 可选 run_analysis_function
       -> 只接收该 SQL 的行
  -> agent_evidence_receipts
  -> 临时 InteractiveReport（引用必须命中本 Turn 回执）
```

`AgentSession` 仍保存长期 Scratchpad 与旧的临时报告兼容行为；`AgentTurn` 只固定一次问题的授权与预算。一个 follow-up 新建一个 Turn。19D 才会把一次实际运行抽成 Attempt，避免现在提前引入一套不完整状态机。

## 3. 快照和工具边界

`query_workspace.py` 只从当前 ticker/run 已归档来源提取已存在的结构化数值，生成两个表：

- `financial_facts`：稳定 `fact_id`、标签、原始 `Decimal` 文本、期间、币种、单位、`source_id`、`locator`、来源哈希；
- `source_metadata`：`source_id`、哈希、种类、标题。

快照不含来源全文、正式报告、Memo、项目主数据库或环境变量。它至多 10,000 条事实，保存在 `data/tmp/analysis/<session>/<turn>/<analysis>/snapshot.db`，写完即改为只读。主数据库绝不执行模型给出的 SQL。

新的 `analysis-workspace` capability Skill v3 授权：

- `describe_analysis_schema`
- `run_readonly_sql`
- `run_analysis_function`
- `read_analysis_artifact`

它也保留既有的 `query_archived_financials` 与 `run_trusted_calculation`，因此 Agent 应先选择固定计算；SQL 是不足时的第二层能力。旧 JSON 数据集/代码工具仍仅用于直接 `ask` 和兼容路径，未泄漏到 v3 Skill。

## 4. SQL、函数与引用怎样受控

`run_readonly_sql` 只接受一条不含分号的 `SELECT` 或 `WITH ... SELECT`，SQL 最多 12,000 字符、400 行、64 KB 输出。它在独立 Python 子进程中以 `mode=ro&immutable=1` 打开快照，并同时使用：SQLite `query_only`、authorizer、progress handler、10 秒 timeout、256 MB 地址空间、文件大小和文件描述符限制。DDL、写操作、`PRAGMA`、事务、`ATTACH`/`DETACH`、`sqlite_master` 读取和扩展加载都会被拒绝。

每个 Turn 最多接受六次已准备的 SQL 执行、三次函数执行。SQL 或函数出错时，主循环把稳定错误码和简短说明回给模型；相同工具触发相同错误会停止，连续三次 QueryWorkspace 错误也会停止当前 Turn。

函数必须是 `def analyze(rows): ... return {...}`。它在另一个无项目环境变量的受限子进程运行，只能处理对应 SQL 已返回的行。AST 允许局部变量、条件、`for` 和少数数值/分组内建函数；拒绝 `import`、属性访问、`while`、递归、lambda、class、异常、文件、网络、SQLite、shell 和动态执行。

SQL 结果只有直接带回真实 `fact_id` 时，程序才生成可引用的 `source_id + locator`。模型最终写入的引用还要逐条匹配同一 Turn 中工具实际返回并记录的 `agent_evidence_receipts`，且核对来源哈希。聚合结果可以辅助推理，却不能凭空变成来源引用。

## 5. 关键文件

- `src/wonder_stockresearch/storage.py`：schema v10、`agent_turns`、Turn 关联的分析记录和证据回执，以及 v9→v10 legacy backfill。
- `src/wonder_stockresearch/interactive/query_workspace.py`：快照构造、SQL/函数隔离、哈希、产物和回执。
- `src/wonder_stockresearch/agent.py`：在 `ask`/`ask-followup` 创建 Turn，向工具传入 Turn，处理可恢复错误并在 final 时校验回执。
- `src/wonder_stockresearch/interactive/agent_tools.py` 与 `tool_registry.py`：四个新工具的 Pydantic 参数和受信任 handler。
- `skills/analysis-workspace/skill.json`：v3 的显式授权集合。

没有单独的“任意 SQL CLI”。这样不会绕开 Agent 的 ticker/run/Turn 归属、Skill allowlist 和来源回执检查。

## 6. 离线验证与不能证明的事

`tests/test_stage19_query_workspace.py` 覆盖：v9→v10 迁移及 legacy Turn、只读快照、SQL/AST 越权拒绝、Turn 级 SQL/函数次数限制、函数计算、证据回执，以及 fake model 先得到可恢复错误再修正查询并完成回答的路径。

```bash
./.venv/bin/python -m pytest -q tests/test_stage19_query_workspace.py
```

这些测试能证明 Python 边界、SQLite 边界和协议在离线 fixture 上生效；不能证明真实模型会稳定挑选最佳 SQL，也不能把应用层和进程资源限制等同于面对任意本机恶意代码的操作系统沙箱。19C 未调用真实 DeepSeek/OpenD，也没有生成、改写或冻结正式研究。

## 7. 小练习

将测试中的 SQL 从带 `fact_id` 的查询改成按期间聚合的查询，例如只返回 `period` 和 `SUM(CAST(value_decimal AS REAL))`。预期：SQL 可以成功，但 `citable=false`，不会产生事实级来源回执。然后把 `fact_id` 加回结果，观察引用如何恢复。

## 8. 下一步

19D 会在已存在的 Turn 边界上增加 Attempt、合法状态迁移、等待用户/失败重试、按 Turn 的报告历史和最小 model adapter，并完成 Quick Start、CI 与面试案例。跨场景质量、成本和延迟评测留到 19E；记忆专项评测和版本化事实记忆仍在更后阶段。
