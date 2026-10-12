# wonder-stockresearch

运行在本地的港股、美股公司研究助手。通过命令行对话、查询公司资料、分析财务指标，并把有价值的临时分析保存到每家公司唯一的主备忘录。

大模型负责理解问题、选择受控工具和解释结果；Python 管理数据来源、期间与单位校验、计算、权限、预算和保存。研究资料、数据库与报告保存在本地 `data/` 中。

**当前处于开发验证阶段。** 固定研究、交互问答和公司主备忘录已有实现；默认使用 Python 问答循环，Pi 运行时为可选预览。最新澄清与数值回答修复已通过离线工程验证，真实五题质量仍待复测。

## 能做什么

| 场景 | 用法与产物 |
| --- | --- |
| 临时分析 | 用 `chat` 或 `ask` 提问，例如分析 ROE 变化；生成带引用与缺口说明的临时回答 |
| 公司备忘录 | 分析成功后输入 `save`；同一股票代码只有一个主备忘录，保留历史版本 |
| 正式公司研究 | 覆盖 Q01–Q24、六项公司门闸和六项护城河评分，经校验与用户确认后定稿 |
| 手动复评 | 新建研究并指定旧版本为 baseline，保留旧报告，比较变化 |
| 过程排查 | 查看本地调用轨迹；可选导出到 Langfuse 等观测服务 |

只研究用户指定的公司，不读取真实账户、持仓、资金或订单，不执行交易。估值 Q25–Q28、股票池筛选、定时任务和 Web UI 尚不在实现范围内。

## 安装与配置

需要 macOS 或 Linux、Python 3.11+ 和 uv。默认问答路径无需 Node.js。

```bash
git clone https://github.com/wonderjz99/wonder-stockresearch.git
cd wonder-stockresearch
uv sync --locked --group dev
uv run wonder-research --help
uv run wonder-research --root . skill-check
```

所有示例均在项目根目录执行。使用其他工作目录时，将 `--root .` 换成项目绝对路径。

参考 [.env.example](.env.example) 在项目根创建自己的 `.env`，填写 `DEEPSEEK_API_KEY`。已有 `.env` 时直接编辑，避免覆盖；环境变量优先于文件配置。密钥和业务数据已被 Git 忽略。

| 配置 | 何时需要 |
| --- | --- |
| `DEEPSEEK_API_KEY` | `chat`、`ask`、草稿写作等模型调用 |
| `WONDER_FUTU_HOST` / `WONDER_FUTU_PORT` | 从富途/OpenD 获取财务或行情；默认 `127.0.0.1:11111`，需自行安装并启动 OpenD |
| `WONDER_SEC_USER_AGENT` | 获取 SEC 披露，填写包含联系方式的 User-Agent |

模型参数等可选设置见模板与[技术规格](docs/technical-spec.md)。模型默认关闭 thinking。已有归档可以离线查询；没有归档时，需要配置可用的数据通道或先导入资料。`allow_network=false` 仅禁止补取资料，回答问题仍可能调用模型 API。

## 开始对话

```bash
uv run wonder-research --root . chat
```

在 `wonder>` 中可以输入：

```text
分析腾讯近四年的 ROE 变化
```

公司简称或期间有必要歧义时，程序会请求确认。数据不足时应说明实际覆盖与缺口，不把已有年份替代为用户要求的年份。

也可以带上首个问题：

```bash
uv run wonder-research --root . chat --text "分析腾讯近四年的 ROE 变化"
```

终端中会继续等待确认或追问；通过管道或重定向运行时返回一次 JSON。长任务进度写到 stderr，最终结果写到 stdout。

### 保存到公司备忘录

本轮分析成功后，在同一对话中输入：

```text
save
```

内容会追加到该公司唯一的主备忘录。同一份回答重复保存不会重复追加，旧版本保留；等待确认或失败的回答不能保存为成功分析。

```bash
uv run wonder-research --root . memo-read HK.00700
```

腾讯主备忘录的 Markdown 文件位于 `data/stocks/HK.00700/memo.md`。保存备忘录不会修改正式研究报告、公司门闸或评分。

## 指定公司与资料范围

需要明确股票代码、资料范围和联网权限时，使用 `ask`。先创建 `data/` 目录，将以下 UTF-8 JSON 保存为 `data/question.json`：

```json
{
  "question": "列出2023至2025年的ROE，并计算均值；单位未知请说明",
  "context": "company_latest",
  "allow_network": false
}
```

然后运行：

```bash
uv run wonder-research --root . ask HK.00700 --input data/question.json
```

`company_latest` 使用当前公司已登记的资料，并为本次问题固定来源版本；不承诺资料完整，也不代表严格历史时点数据。这个离线补取示例需要已有归档；若希望允许补取，将 `allow_network` 改为 `true` 并配置数据通道。直接 `ask` 默认使用最近已完成报告，以上 JSON 显式选择了公司资料范围。

返回结果中的 `session_id` 与 `turn_id` 用于查看状态、补充和恢复。下面的 `SESSION_ID`、`TURN_ID` 是占位符，需替换为实际值：

```bash
uv run wonder-research --root . ask-status SESSION_ID
uv run wonder-research --root . agent-trace SESSION_ID
uv run wonder-research --root . memo-save SESSION_ID
```

等待澄清时，将补充信息保存为 `data/response.json`，形状如 `{"question": "我指的是2023、2024、2025三个完整自然年"}`，再运行：

```bash
uv run wonder-research --root . ask-respond SESSION_ID TURN_ID --input data/response.json
```

失败或中断的目标可用 `ask-retry SESSION_ID TURN_ID` 恢复；继续提出新问题用 `ask-followup SESSION_ID --input data/followup.json`，输入也是 `{"question": "你的追问"}`。补充和重试延续原目标的累计预算，不重置额度。具体条件见各命令 `--help`。

## 正式公司研究

在 `chat` 中输入“帮我研究一下中国移动”，确认公司与研究意图后，程序采集资料、写草稿并校验，再由你确认是否定稿。

显式 CLI 流程如下，`RUN_ID` 替换为 `start` 返回的实际值：

```bash
uv run wonder-research --root . start HK.00941
uv run wonder-research --root . collect RUN_ID
uv run wonder-research --root . write-draft RUN_ID
uv run wonder-research --root . validate RUN_ID
```

读过草稿、核对证据并决定定稿后，执行 `uv run wonder-research --root . finish RUN_ID`。正式报告完成后冻结，后续更新应新建研究；临时问答与主备忘录独立保存。研究问题、证据规则和评分含义见[研究规格](docs/research-spec.md)。

## 架构

```text
CLI / chat
    ├── 正式研究：采集 → 草稿 → 校验 → 用户 finish → 冻结报告
    └── 临时问答：Python 默认循环 / Pi 可选预览
                        ↓
              Python 受控工具与确定性校验
                        ↓
         wonder.db + company.db + 原文与分析快照
                        ↓
              临时回答 → 用户 save → 公司主备忘录
```

[Python 源码](src/wonder_stockresearch)负责领域规则、持久化与授权；[项目 Skills](skills)提供研究与问题澄清方法。SQL 在隔离只读快照中执行，计算使用受限进程；Skill 文本本身不授予额外权限。

数据库、来源、报告和导出文件均放在 `data/`，不随仓库分发。更完整的数据模型、运行时与兼容约定见[技术规格](docs/technical-spec.md)，框架及依赖出处见[出处说明](docs/provenance.md)。

## 可选：Pi 与 Langfuse

Pi 预览需要 Node.js 22.19+。在项目根安装锁定的运行时依赖：

```bash
npm --prefix src/wonder_stockresearch/pi_runtime ci --omit=dev --ignore-scripts --no-audit --no-fund
WONDER_AGENT_RUNTIME=pi uv run wonder-research --root . chat
```

仓库附带构建后的 `runner.mjs`，运行不需 TypeScript 编译器。Pi 的真实质量验收尚未通过，默认路径仍为 Python；旧问题按创建时固定的运行时恢复。

Langfuse 的凭据字段见 [.env.example](.env.example)。配置后可显式启用导出：

```bash
WONDER_TRACE_EXPORTER=langfuse uv run --extra observability wonder-research --root . chat
```

默认只导出安全摘要；若显式开启 `WONDER_TRACE_CAPTURE_CONTENT=preview` 或 `full`，可见问题、回答和工具内容也会发送到所配置的外部服务。本地 `agent-trace` 可直接查看安全调用轨迹，不需要 Langfuse。

## 开发与验证

快速检查只需要 Python 开发依赖。全量测试同时覆盖 Python 与 Pi 路径，因此还需要 Node.js 22.19+，并先执行上面的 Pi 依赖安装命令；只使用默认问答路径时无需安装 Node。

```bash
uv run python scripts/dev_check.py check
uv run python scripts/dev_check.py check --full
```

快速检查覆盖公开文档链接、10 项源码默认值、diff 与 Ruff；`--full` 另跑离线 pytest，缺少 Pi 依赖会明确失败。个人开发文档和历史归档存在时也会校验，公开克隆无需这些本地材料。报告写入 `data/dev-checks/`，检查不调用模型或 OpenD，也不自动安装依赖。

当前限制：

- 离线测试验证工程契约，不能证明真实模型稳定理解问题、选对工具或解释经营原因。
- Pi 历史 30 题人工审阅结果为 4 通过、5 部分通过、21 失败；最新修复的真实质量仍待复测，执行完成率不能作为回答正确率。
- 程序已支持原值、均值、极差、总体标准差和期间变化的数值契约；更多公式、指标口径与单位映射仍待完善。
- 单位未知、覆盖不足或资料冲突时，需要保留不确定性并核对原始来源。

## 许可证

[MIT License](LICENSE)。第三方框架与依赖的来源和许可说明见[出处说明](docs/provenance.md)。
