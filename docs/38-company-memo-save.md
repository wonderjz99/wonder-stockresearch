# 每家公司一个备忘录，分析后输入 save

2026-10-11。本阶段承接临时分析与失败恢复，增加用户显式保存入口。每个 ticker 只有一个主备忘录，按问题追加分析；不调用模型归纳、不重新计算数值、不修改正式研究。

## 1. 解决什么问题

此前 `memo-save` 要自己准备 JSON，而且每次保存得到独立 Memo。现在正常完成临时分析后，在同一个 `wonder>` 提示符输入 `save` 即可。程序把当前问题的原回答、结论、反方、缺口、后续核查和全部引用保存到公司主备忘录。原结论不会因为保存而变成已证实事实。

腾讯对应 `HK.00700`，主备忘录文件固定为 `data/stocks/HK.00700/memo.md`。重复 save 同一个报告不增加条目或版本；之后另一条分析保存时，追加到同一文件和同一主 ID。

## 2. 实际调用图

```text
临时分析完成 → wonder> save
  → chat 精确命令分支（Router/LLM 不参与）
  → memo_save_report：选择当前 Turn，检查报告归属和来源
  → 同一事务：不可变条目 + 公司关联/版本 + 长期记忆索引
  → 导出该公司的 memo.md → 保存反馈 → 仍可追问或退出
```

仅识别独立的 `save`、`/save`、`保存`，忽略首尾空格和英文大小写。没有当前 chat、仍在确认、当前 Turn 失败/等待用户时拒绝快捷保存，不会偷偷选上一条成功回答。save 是用户明确的写入授权，Agent 工具仍只读。

## 3. 文件与入口

| 文件/函数 | 职责 |
| --- | --- |
| [chat.py](../src/wonder_stockresearch/chat.py) `run_once()` | 在路由前识别 save，保存当前关联 Agent 会话；成功返回 `memo_saved` 和文件位置，失败可继续操作。 |
| [agent.py](../src/wonder_stockresearch/agent.py) `memo_save_report()` | 读取已保存的 `InteractiveReport`，校验 Session/Turn/ticker、来源及 company_latest 回执；合并报告级和 claims 引用，确定性生成条目。 |
| [company_memo.py](../src/wonder_stockresearch/interactive/company_memo.py) `attach()/read()/render()/export()` | 管主备忘录唯一性、顺序版本、历史读取和 Markdown 导出。 |
| [storage.py](../src/wonder_stockresearch/storage.py) schema v14 | `company_notebooks.ticker` 主键保证每公司一个主 Memo；`company_memo_links.report_turn_id` 唯一保证重复快捷保存幂等。原 `company_memos` 存不可覆盖的条目。 |
| [cli.py](../src/wonder_stockresearch/cli.py) | 分析后提示 save，保存后继续终端循环；支持不传 JSON 的 memo-save 及按 ticker/版本读取。 |
| [agent_tools.py](../src/wonder_stockresearch/interactive/agent_tools.py) | Agent 的 list_memos 返回单个公司主备忘录，read_memo 仍核对公司；旧条目 ID 保持可读。 |

迁移把已有公司 Memo 按公司、保存时间和旧 ID 顺序归入主备忘录，不改旧内容、旧 ID 或标题版本。既有多个版本作为历史记录保留，不猜哪份可以丢弃。主备忘录版本是追加次数；历史版本由不可变条目的前缀重建。

## 4. 怎么使用

原命令继续可用：

```bash
uv run --extra observability wonder-research --root . \
  chat --text "分析腾讯近四年的 roe 变化"
```

当 stdin/stdout 都是终端时，显示分析后进入原 chat 的提示符：

```text
输入 save 加入该公司的备忘录，或继续追问。
wonder> save
已加入 HK.00700 公司备忘录（版本 1，共 1 条）。
文件：<项目根>/data/stocks/HK.00700/memo.md
wonder>
```

没有多一步确认，也不用复制分析正文或编写 JSON。这个例子中的真实分析命令本轮未运行。

如果刚才的终端已经退出，或它是在本次代码更新前启动的旧进程，用之前显示的 **chat session_id** 恢复并保存：

```bash
uv run --extra observability wonder-research --root . \
  chat --session-id CHAT_SESSION_ID --text save
```

终端双 TTY 情况下会继续等待；管道/重定向输出一次 JSON。程序不根据“全项目最新会话”猜保存目标，避免保存错公司。

查看主备忘录及历史：

```bash
uv run wonder-research --root . memo-list HK.00700
uv run wonder-research --root . memo-read HK.00700
uv run wonder-research --root . memo-read HK.00700 --version 1
```

直接 `ask` 用户可以用 **Agent Session ID** 快捷保存；必要时显式选择同会话中某个已完成问题：

```bash
uv run wonder-research --root . memo-save AGENT_SESSION_ID
uv run wonder-research --root . memo-save AGENT_SESSION_ID --turn-id TURN_ID
```

原 `memo-save AGENT_SESSION_ID --input data/memo.json` 仍支持自定义条目和标题版本，也归入同一公司主备忘录；不能与 `--turn-id` 同用。它是自定义记录保存，不采用快捷保存的报告去重语义。

## 5. 为什么这样实现

数据库主键像一张身份证：ticker 唯一，所以更换问题标题不会另建公司主备忘录。条目单独保存，相当于给一本笔记不断加页，不覆盖旧判断，也不擅自消除前后矛盾。

所有内容直接来自已完成报告，save 不调用 Router、LLM 或 OpenD，也不消耗模型预算。没有引用的缺口回答可以保存，但会明确显示“没有来源引用”；手工 MemoRequest 仍要求至少一个真实引用。claims 内的引用也会收集，不能只看顶层 evidence_refs 是否为空。

条目、关联和长期记忆在同一数据库事务中提交，任何一步失败会回滚。SQLite 是权威内容，Markdown 是可再生视图。若提交后文件导出失败，会明确告诉用户数据库已保存；再次 save 修复文件，不重复追加。导出路径限制在项目 data 内。会话锁与项目写锁避免分析执行和保存相互覆盖。

## 6. 实际验证

新增 [test_company_notebook.py](../tests/test_company_notebook.py) 使用虚构来源和脚本模型，覆盖两 runtime 的快捷保存、重复保存、连续追问追加、缺口与 claims-only 引用、重启恢复命令、当前失败不保存旧答案、跨会话/公司拒绝、历史读取、v13 迁移、事务回滚、导出失败修复、CLI/终端提示与循环。

```bash
./.venv/bin/python -m pytest -q tests/test_company_notebook.py
./.venv/bin/python -m pytest -q
WONDER_AGENT_RUNTIME=pi ./.venv/bin/python -m pytest -q
./.venv/bin/ruff check src tests scripts
```

最终默认/Pi 全量各 **416 passed**，Ruff、wheel 构建与隔离加载通过；具体命令结果见 [PROGRESS](../PROGRESS.md)。旧 Memo 列表测试按新的“主备忘录 + 不可变条目”语义增强断言；旧迁移夹具补上排除 v14 表，预算/报告保留断言继续验证。没有改主库或运行真实分析来测试。

## 7. 一个小练习

连续输入两次 save，预期第二次显示“没有重复添加”，主版本不变。随后追问另一个问题，完成后 save，预期主 ID 和文件路径不变、版本加一；读 `--version 1` 仍只有首次保存内容。

## 8. 限制与下一步

当前按问题和保存顺序追加，不做模型主题归类、总结或冲突裁决；需要用户阅读来源并判断。很长的备忘录进入 Agent 上下文时仍受原投影预算约束，CLI 和文件可查看完整内容。

schema v14 在用户下次运行程序时自动事务迁移。旧 Memo 的迁移只改索引关系，不自动导出文件，后续 save 会生成；已有数据库备份包含全部条目，memo.md 是可再生文件。每公司以 ticker 区分，不自动合并腾讯港股与其他上市代码。

未新增真实模型/OpenD 调用、依赖或 Git 提交，默认运行时和真实评测额度不变。公司展开视图、SQL 优化和指标单位映射仍按独立阶段实施。本教学阶段完成后暂停。
