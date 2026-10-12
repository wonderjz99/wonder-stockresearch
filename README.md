# wonder-stockresearch

个人港股、美股公司研究助手，也是分阶段学习Python研究应用的项目。通过项目自身CLI、DeepSeek与富途/OpenD或已归档资料运行，不依赖OpenCode等外部宿主。

当前固定研究、交互问答、公司事实投影与公司主Memo已有实现；默认问答路径为legacy，Pi为显式预览。最新澄清/数值回答修复已完成离线验证，真实五题质量待复测。完整实际状态见 [PROGRESS](PROGRESS.md)，当前契约见 [技术规格](docs/technical-spec.md)。

## 开发与继续会话

AI先按 [AGENTS](AGENTS.md) 读取现行规格，再核对代码/工作区与测试。你可以直接说：

> 按项目开发流程，解决这个问题：……。希望输入……时得到……。本轮完成一个可验收结果，常规实现自行决定，完成验证、中文讲解和进度交接。

[开发流程教学](docs/development-workflow.md)包含规格怎么用、提示词示例、验收方法和自动化边界；[变更模板](docs/change-template.md)由AI按需维护，你不必每次手写。当前任务见 [PLAN](PLAN.md)，已做过的工作见PROGRESS，旧决定见 [决策索引](docs/decisions.md)。

本地开发入口（项目根、已有开发依赖）：

```bash
./.venv/bin/python scripts/dev_check.py context
./.venv/bin/python scripts/dev_check.py check
./.venv/bin/python scripts/dev_check.py check --full
```

检查报告/日志保存在忽略的data/dev-checks。快速check检查现行文档、10项源码默认值、历史哈希、diff和Ruff；--full另跑离线pytest。按改动可加--tests、--pi或--node，详见教学。命令不读取业务库/.env，不启动研究或真实评测。

## 产品边界

- 指定股票研究、本地手动清单、历史保存、手动复评；不读取账户/持仓/资金或交易。
- 标准报告覆盖Q01–Q24、六项公司门闸、六项护城河评分；估值Q25–Q28独立延后，价格不能改公司分数。
- 来源、期间、口径与缺口可追溯；模型判断仍需核验。
- 固定正式报告、交互临时回答、公司主Memo三种产物独立；用户明确finish或save才归档相应产物。
- 不做股票池筛选、定时任务、Web UI、向量数据库或交易建议引擎。

研究含义见 [研究规格](docs/research-spec.md)。

## 安装与配置

目标为macOS/Linux、Python >=3.11与uv；开发依赖已在pyproject/uv.lock声明。安装/常用命令：

```bash
uv sync --group dev
uv run wonder-research --help
uv run wonder-research --root . skill-check
```

联网模型命令需要用户自行配置DEEPSEEK_API_KEY。可使用环境变量或项目根.env；环境变量优先，.env被Git忽略，不提交/不打印。只保留无密钥.env.example模板；已有.env时不要用复制命令覆盖它。

默认模型配置、问答预算和历史兼容以技术规格为准。项目默认关闭thinking，配置可覆盖。行情/财务补取需OpenD，默认127.0.0.1:11111，可用WONDER_FUTU_HOST/WONDER_FUTU_PORT配置；只使用行情上下文。SEC需WONDER_SEC_USER_AGENT，不可用只阻塞对应通道。

Pi预览需要项目内Node依赖与构建文件，安装、锁定版本及限制见 [Pi运行时](docs/30-pi-agent-runtime.md)。预览质量门槛尚未通过，不能仅因可运行就当作推荐默认。

## 使用

自然语言入口可能调用模型和只读数据源：

```bash
uv run wonder-research --root . chat
uv run wonder-research --root . chat --text "帮我研究一下中国移动"
```

正式研究先确认公司/意图，再采集、写草稿、validate，最后由你确认finish。简称有歧义先选公司，不自动猜测。TTY中--text作为首轮并继续会话；管道/重定向时返回一次JSON。长任务进度写stderr，stdout保留结果。

开放问题也可用UTF-8 JSON文件明确参数：

```bash
uv run wonder-research --root . ask HK.00700 --input data/question.json
uv run wonder-research --root . ask-followup <SESSION_ID> --input data/followup.json
uv run wonder-research --root . ask-status <SESSION_ID>
uv run wonder-research --root . ask-respond <SESSION_ID> <TURN_ID> --input data/response.json
uv run wonder-research --root . ask-retry <SESSION_ID> <TURN_ID>
```

ask默认资料范围与chat不同，完整参数先看ask --help；JSON示例可在data/question.json自行保存：

```json
{
  "question": "列出2023至2025年的ROE，并计算均值；单位未知请说明",
  "context": "company_latest",
  "allow_network": false
}
```

allow_network=false禁止补取，已有资料不足会明确缺口。相对期间有必要歧义先确认，补充继续原Turn；年份、原值、单位和引用由共用校验处理，不能把结构合格当作解释已证实。

成功临时分析后在wonder>输入save，保存到该公司唯一主备忘录。重复保存同一报告不会重复追加，历史版本保留：

```bash
uv run wonder-research --root . memo-save <SESSION_ID>
uv run wonder-research --root . memo-read HK.00700
```

腾讯导出路径为data/stocks/HK.00700/memo.md；仅用户明确保存，不自动修改正式研究或评分。详见 [主Memo教学](docs/38-company-memo-save.md)。

固定研究的显式命令流程：

```text
start TICKER → collect RUN_ID → write-draft RUN_ID
→ validate RUN_ID → 用户明确finish RUN_ID
```

write-draft默认分组写作/审查，只保存草稿；失败可恢复成功分组。复评用start --baseline与compare，备份用backup --output data/backups/新目录。精确选项与行为见命令--help和技术规格。

## 架构与排查入口

```text
CLI / chat → Python路由与确认
             ├─ 固定研究：证据包 → Writer/Review → validate → 用户finish
             └─ 开放问答：legacy默认 / Node-Pi显式预览
                              ↓
                   Python受控工具、证据、计算与预算
                              ↓
                wonder.db + company.db + 原文/隔离快照
                              ↓
                    临时报告 → 用户save → 公司主Memo
```

- [架构总览](docs/33-current-architecture.md)、[执行架构与Pi钩子](docs/35-agent-execution-architecture.md)。
- [查询/恢复教学](docs/37-agent-query-and-recovery.md)、[澄清/数值回答教学](docs/40-question-clarification-and-numeric-answers.md)。
- [公司库](docs/28-company-data-store.md)、[共享查询与来源固定](docs/29-company-agent-query.md)。
- [执行恢复](docs/23-agent-runtime-reliability.md)、[trace/Phoenix/Langfuse](docs/16-tracing-and-phoenix.md)。
- [五题复盘](docs/39-five-question-review-and-general-plan.md)、[Pi历史验收](docs/31-pi-agent-evaluation.md)。
- [出处](docs/provenance.md)、[教学约定](docs/teaching-contract.md)、[整理前README及完整旧导航](docs/history/20261012-before-workflow/README.md)。

资料、数据库、运行报告保存在data并被Git忽略。模型判断、单位/期间及语义解释仍需研究验收；离线检查不触发真实模型/OpenD。当前真实额度与下一步见PLAN。

## 许可证

本项目采用 [MIT License](LICENSE)。
