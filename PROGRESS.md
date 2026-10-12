# 项目当前进度

本文件是实际结果与限制的短入口；后续顺序见 [PLAN](PLAN.md)，规则查 [研究规格](docs/research-spec.md) / [技术规格](docs/technical-spec.md)。截至2026-10-11的全部原始进度记录保存在 [历史全文](docs/history/20261012-before-workflow/PROGRESS.md)，未删除旧失败/决定或改写成绩。

## 当前产品状态

- 固定研究、来源归档/校验、版本保存与复评、Skill、自然语言入口、交互Session/Turn/Attempt、QueryWorkspace、公司事实投影A/B、主Memo/save已实现。
- 最新字段检索/工作清单/错误恢复及澄清/数值回答工程已有离线验证，真实五题仍待复测。最近既有阶段默认/Pi全量各457 passed，详见 [原记录](docs/history/20261012-before-workflow/PROGRESS.md) 与 [教学40](docs/40-question-clarification-and-numeric-answers.md)。
- Pi为显式预览，legacy仍默认。历史真实40/40耗尽；30题最后审阅4 pass/5 partial/21 fail，不能把34 completed当作34通过。见 [原验收](docs/31-pi-agent-evaluation.md)。
- 固定研究草稿通过自动校验不等于用户已验收或正式冻结；本次开发整理未查询/迁移主库或改变任何研究状态。
- D1展开视图、D2全面SQL下推/快照复用、D3剩余单位/口径映射、同条件真实质量验证及上手体验仍有待办，见PLAN。

## 2026-10-12：规格与开发流程整理（完成）

用户要求参考近期spec/vibe coding方法，整理现行规则和历史，并教学后续使用及自动化空间。本次开发任务见 [变更单](docs/changes/20261012-spec-workflow.md)，使用与方法来源见 [中文教学](docs/development-workflow.md)。

实际文件：整理README/AGENTS/PLAN/本进度、技术规格和教学约定；研究规格仅补文档职责说明，09/27补现行入口提示，出处补方法参考。新增docs/development-workflow.md、change-template.md、decisions.md、changes/20261012-spec-workflow.md、历史快照及manifest、scripts/dev_check.py、tests/test_dev_check.py。当前规则与历史分离，阶段内常规步骤不增审批；未引入spec框架运行依赖。

实际验证：

- `./.venv/bin/python scripts/dev_check.py context`：exit0，读取顺序/10项源码默认值/工作区与变更入口正常，不查询业务库。
- `./.venv/bin/python scripts/dev_check.py check --tests tests/test_dev_check.py`：exit0，新增 **15 passed**；覆盖错值/重复默认行、源码变化、缺链接/坏围栏、历史哈希、凭据继承过滤、失败命令留证与JSON失败退出、参数拒绝和输出越界。
- 最终 `./.venv/bin/python scripts/dev_check.py check --full`：exit0，**472 passed**，Ruff、diff、11份现行入口/变更单90个相对链接、10项默认值、7份档案哈希均通过；报告为 `data/dev-checks/20261011T163753Z-b0ccf5ee/report.json`。首次全量测试已通过但Ruff发现脚本re.M缩写，改为re.MULTILINE后定向和最终检查通过，失败报告保留。
- 额外检查09/27/provenance后共14份文档123个相对链接通过；当前入口的本地标题片段人工脚本核对无缺项。7份档案去提示/反向重定位后恢复原SHA-256；118份既有业务源码/测试/Skill/配置哈希均未变，证据为 `data/dev-workflow/20261012-verification.json`。一开始额外核对误用系统Python3.9导入工具失败，改用项目Python后通过；入口文档统一使用项目解释器。

限制：脚本只核对10项标量及列出的本地检查，不能发现所有自然语言矛盾或证明真实模型质量；离线测试约束依赖fake，不是OS网络沙箱。没有Pi业务/TS改动，未重复Pi默认全量、TS构建或wheel；新增工具仅开发期使用，不纳入产品安装包。CI/Git hook/真实评测统一入口为候选，未启用。

新研究模型/OpenD调用0，未重置40次、未迁移主库、未改业务源码/默认运行时、未提交Git、未安装框架或启用CI/定时任务。后续可用本次指南直接提一个可验收问题；研究侧优先同条件五题复测，仍需新的明确额度。本教学阶段完成后暂停。
