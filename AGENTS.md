# wonder-stockresearch — AI 开发约定

## 开工入口与文档职责

按顺序读 README.md、PLAN.md、docs/research-spec.md、docs/technical-spec.md、docs/teaching-contract.md、PROGRESS.md；随后核对当前代码、`git status --short` 和相关测试。涉及出处读 docs/provenance.md；开发流程见 docs/development-workflow.md。可运行 `./.venv/bin/python scripts/dev_check.py context` 获取只读交接摘要。

- research-spec：研究方法与证据规则的唯一现行正文。
- technical-spec：数据、CLI、运行时、预算与兼容规则的唯一现行正文。
- PLAN：下一步及验收顺序；PROGRESS：实际结果与限制。
- docs/changes：单次变更的目标、范围、验收与证据；docs/decisions.md：决策索引。
- docs/history 与编号教学章：历史/解释材料，按任务查阅，不作为覆盖现行规格的补丁。

用户最新指令优先。发现规格与代码不一致，明确报告差异；不能仅因代码这样实现就悄悄改需求，也不能把历史提案当作现行授权。

项目自足，不查旧聊天或依赖 LLM-trade1 的代码、数据库、配置、规则与私有数据；不复制旧大型 MCP、七阶段状态机或事件重放系统。

## 产品与权限边界

1. 个人港股、美股指定公司研究，其次是历史保存和手动复评；只维护本地手动清单。
2. 不读取真实账户、持仓、资金或订单；禁止交易、解锁交易、修改外部自选分组。
3. 不请求、打印或保存交易密码、模型 API Key、账户秘密。Python 从环境变量或用户自行配置的项目根 `.env` 读取凭据；`.env` 保持 Git 忽略，仓库只留无密钥 `.env.example`。
4. 公司与估值分离，价格不能改公司门闸/评分；估值的现行范围见 research-spec。
5. 不做股东信全文、Deep、股票池筛选、定时任务、日报周报、Web UI、向量数据库或交易建议引擎。
6. 默认每次完成一个可独立验收的教学阶段后暂停；用户明确要求多阶段时按最新指令执行。阶段内常规实现、修复与验证连续完成，不增加逐步批准关卡。
7. 新文件只放本项目。软件业务数据/日志/报告只写 `data/`；用户指定导入文件可只读读取。源码、测试和开发文档属于项目开发文件。

## 实施方法

- Python 核心 + CLI + sqlite3/文件；Pydantic 校验、argparse、普通函数和小模块，不引入 ORM、仓储接口层、依赖注入容器或插件框架。
- 项目独立提供固定研究、ask/chat、Skill Registry、Memo。Python 权威管理授权、证据、计算、预算、Session/Turn/Attempt 和持久化；项目自带 Node/Pi 预览的现行职责与锁定依赖见 technical-spec。不依赖 OpenCode 或外部宿主，不引入多 Agent/LangGraph。
- 研究输入用 UTF-8 JSON 文件或 stdin，不用 shell 拼接正文；stdout 为最终 JSON，长任务进度和日志写 stderr，不输出隐藏推理、秘密、完整原文或完整工具结果。
- 模型提出待核验判断；程序计算、校验引用和渲染。结构通过、工具成功、completed 均不能代替研究质量验收。
- 非微小改动先用 docs/change-template.md 建立一页变更单：真实问题、预期行为、范围之外、验收和验证方式。明确授权下直接实施；只有影响产品范围、研究方法、外部权限或真实调用额度的未决选择才询问。
- bug 先复现或保存失败证据，再修共用机制；不得按公司/ticker/题号/完整问题写特例。必要测试按可观察行为设计，不能为变绿弱化原授权、预算、引用或冻结断言。
- 核对已有工作区改动，只修改本次范围。不要自行初始化 Git、提交、安装扩展、发布或部署。每阶段可建议可审阅的提交边界，由用户决定提交。

## 交互 Agent 参考与产物边界

修改交互 Agent 前读 docs/09-agent-layer.md，参考用户指定 Dexter 的 [主循环](https://github.com/virattt/dexter/blob/main/src/agent/agent.ts)、[工具注册](https://github.com/virattt/dexter/blob/main/src/tools/registry.ts)、[Skill注册](https://github.com/virattt/dexter/blob/main/src/skills/registry.ts)、[Scratchpad](https://github.com/virattt/dexter/blob/main/src/agent/scratchpad.ts)。仅借鉴受限循环、元数据、轨迹、预算和循环检测，保留项目契约。

固定 ResearchRun、临时 InteractiveReport、公司主 Memo 各自独立。Agent 不改正式研究、门闸/评分或执行 finish。只有用户明确 memo-save/chat save 才由 Python 保存公司主 Memo；不授予 Agent 写权限。自然语言正式研究启动与 finish 都需用户确认。

## 验证与收尾

- 默认离线测试，fake 替代网络/模型/OpenD；真实 smoke 单独执行与记账，不因开发指令自动重置既有额度。当前真实验证权限见 PLAN。
- 文档/规格整理：`./.venv/bin/python scripts/dev_check.py check`。
- 定向代码检查：上条命令加 `--tests tests/test_x.py`；不要每个小改动都重复全量。
- 跨模块、持久化、授权/预算等改动：`./.venv/bin/python scripts/dev_check.py check --full`；涉及两问答运行时共用规则加 `--pi`，涉及 Pi TS/生成文件加 `--node`。先完成适用检查，相关结果通过后不无理由重复。
- 脚本输出只代表其列出的检查；缺依赖如实失败，不自动安装。自然语言一致性、代码审查、真实质量仍由本次验收补充。
- 收尾复核 diff 与验收；只在规则变化时改对应规格，在 PLAN 更新待办，在 PROGRESS 记真实文件/命令/结果/限制。教学与任务证据用链接连接，避免多份复制同一规则。
- 如仅通过离线验证，明确写“工程验证通过，真实质量待复测”；不要预填结果。代码、必要测试、中文讲解与交接齐全后暂停。
