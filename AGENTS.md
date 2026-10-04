# wonder-stockresearch — 给后续 Codex 会话的工作约定

## 必须先读

按顺序阅读 README.md、PLAN.md、docs/research-spec.md、docs/technical-spec.md、docs/teaching-contract.md、PROGRESS.md。涉及框架出处时读 docs/provenance.md。随后检查当前代码、工作区改动和实际测试；不要仅凭进度文档宣称完成。

这是一份自足的新项目交接。不要寻找旧聊天记录或依赖 LLM-trade1 的代码、数据库、配置、规则文件与私有数据。不要复制旧项目的大型 MCP 服务、七阶段状态机或事件重放系统。

## 用户已确认的边界

1. 个人港股、美股公司研究；首要任务是指定股票研究，其次是历史保存和手动复评。
2. 手动清单，不读取真实账户、持仓、资金或历史订单；禁止交易、解锁交易及修改外部自选分组。
3. 不请求、打印、保存交易密码、模型 API Key 或账户秘密。DeepSeek 凭据由本项目 Python 程序从用户自行配置的环境变量读取；经用户明确要求，也可在项目根放置本地 `.env` 并由 `envfile.py` 加载。`.env` 必须在 `.gitignore` 中、不提交、不打印，仓库只保留无密钥的 `.env.example`。
4. 公司问题与估值问题分开。公司证据决定公司门闸与评分；价格和估值不能改公司分数。
5. 不用股东信全文；不做 Deep、股票池筛选、定时任务、日报周报、Web UI、向量数据库或交易建议引擎。
6. 每次只实现下一个教学阶段：代码、必要测试、中文讲解和进度更新完成后暂停，由用户决定继续。用户明确要求多阶段时按其最新指令执行。
7. 旧项目保持不变；本项目新增文件都在本项目内。软件所有业务文件操作也限制在项目 data/ 下，用户明确提供的待导入文件允许只读读取。

## 工程方法

- 采用 Python 核心 + CLI + SQLite/文件；固定研究流程、交互 Agent、Skill Registry 与自然语言入口都必须由本项目独立运行，不依赖 OpenCode 或其他外部宿主。用户自建 Agent 系统可在需要时调用本项目 CLI/API，但不是研究、会话或报告的唯一来源。DeepSeek API、富途/OpenD 或其他已配置数据源由程序直接读取。LangGraph 只在真实运行证明存在复杂动态编排需求后评估；不能提前替代函数核心。
- 普通函数、小模块、明确数据模型。使用 Pydantic 输入校验、sqlite3 直接 SQL、argparse CLI；不做 ORM、仓储接口层、依赖注入容器或插件框架。
- 研究参数和文字通过 UTF-8 JSON 文件或 stdin 传递；禁止 shell 拼接研究正文。业务输出 JSON 到 stdout，日志到 stderr。
- 模型产生待验证的结构化判断；程序处理数据、计算总分、验证引用并渲染报告。结构合格不等于判断已证实。
- 不把数据库、下载资料或运行报告提交 Git；不自行初始化 Git、提交或安装额外扩展，除非用户要求或正常开发所需且授权适用。
- 离线测试不访问网络/OpenD/账户。需要真实接口时显式运行独立 smoke 验证，报告结果和限制。
- 文档与实际实现一致。不存在的命令标明规划；没有运行的测试标明未运行；不得预填通过结果。

## 交互 Agent 的参考实现

实现或修改交互式 Agent 时，必须先阅读本项目的 `docs/09-agent-layer.md`，并参考用户指定的 Dexter 项目：

- [Dexter GitHub](https://github.com/virattt/dexter)
- [Dexter Agent loop](https://github.com/virattt/dexter/blob/main/src/agent/agent.ts)
- [Dexter tool registry](https://github.com/virattt/dexter/blob/main/src/tools/registry.ts)
- [Dexter skills registry](https://github.com/virattt/dexter/blob/main/src/skills/registry.ts)
- [Dexter scratchpad](https://github.com/virattt/dexter/blob/main/src/agent/scratchpad.ts)

参考重点是“主 Agent 循环、受限工具注册表、Skill 元数据、调用轨迹、预算和循环检测”，不是复制 Dexter 的 Bun/TypeScript/LangChain 技术栈。任何改编都必须保持本项目的 Python/CLI/SQLite 核心、证据定位和正式报告冻结规则。

当前交互 Agent 必须是独立程序能力：用户只配置 `DEEPSEEK_API_KEY` 以及富途/OpenD 或已归档资料，就可以从终端运行 `ask`、`ask-followup` 和 Memo 命令。不要求安装、启动或集成任何外部宿主；OpenCode 相关文件仅作历史记录，不作为后续实现入口。

当前自然语言入口 `chat` 也必须独立运行：Python 的 Skill Registry 读取项目 `skills/**/SKILL.md`，Router 只返回经 Pydantic 校验的 RoutePlan，真正的采集、写作、校验、定稿仍由现有 Python 服务执行。非严格中文表达可以触发候选流程，但正式研究启动和 `finish` 都必须经过用户确认。

`collect`、`write-draft`、`ask` 和 `chat` 的长任务进度只写 stderr；stdout 保留最终 JSON。不得打印模型隐藏推理、密钥、完整原文或完整工具结果。

当前只有一个完整交互 Agent；自然语言 Router 是一次受控分类组件，正式报告 Writer 是一次受控写作组件。不要因为增加 `chat` 就引入多 Agent、TypeScript runtime 或 LangGraph。

固定研究报告、交互临时报告、公司 Memo 是三种不同产物。交互 Agent 不得自动修改 `ResearchRun`、评分、门闸或执行 `finish`；只有用户明确调用 `memo-save` 才能保存 Memo。

## 决策与进度

用户自然语言新指令优先。规则变更写入对应规格并在 PROGRESS.md 留简短决策记录；不悄悄改变评分意义、研究范围或阶段验收。

规格中明确标记的工程默认值由实施者执行，不需要反复询问用户。影响产品范围、研究方法或外部权限的变更才需说明并确认。常规实现细节自行解决。

每阶段完成后更新 PROGRESS.md：实际文件、运行命令及结果、已知限制、下一阶段。读取必要文件后直接工作，不重复做一轮已完成的需求访谈。
