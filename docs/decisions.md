# 当前决策索引

本页解释关键决定并链接证据，不复制完整规则。现行行为查research-spec/technical-spec；未来候选查PLAN；历史日期只表示当时决定，不自动授予新外部权限。

| 日期 | 决定与原因 | 现行契约/证据 |
| --- | --- | --- |
| 2026-09-10起 | 独立个人公司研究项目，保留Python/CLI/SQLite与普通函数；与旧项目隔离 | [研究规格](research-spec.md)、[技术规格](technical-spec.md) |
| 2026-09-18 | 标准研究定稿冻结，复评创建新run；附记/估值及专题仅预留 | [报告层级](research-spec.md#9-报告层级与附记用户-2026-09-18-决定) |
| 2026-09-21 | 直接DeepSeek Writer使项目可独立写作；用户可自行配置忽略的.env，模型不直接冻结报告 | [Writer契约](technical-spec.md#8-固定writer与审查)、[原阶段记录](history/20261012-before-workflow/PROGRESS.md) |
| 2026-09-21 | provider已报告数据与实时快照用不同时间资格，不把取得时间当披露时间 | [来源规则](research-spec.md#6-来源与陈述) |
| 2026-10-07 | Session/Turn/Attempt区分用户目标与执行，恢复保留预算和历史 | [当前交互契约](technical-spec.md#10-独立交互agent数值回答与公司memo)、[教学23](23-agent-runtime-reliability.md) |
| 2026-10-08 | 公司事实投影独立，按Turn固定来源/解析版本，复用归档不扩权 | [公司查询](29-company-agent-query.md) |
| 2026-10-09 | 用户批准Python领域核心+项目内Node/Pi；先预览和验收，替代早期不引入TS运行时的阶段限制 | [运行时契约](technical-spec.md#1-模块与依赖)、[验收失败与额度](31-pi-agent-evaluation.md) |
| 2026-10-09—11 | 真实失败推动工作清单、预算、错误恢复、澄清与事实身份渲染；离线通过不替代真实质量 | [教学37](37-agent-query-and-recovery.md)、[教学40](40-question-clarification-and-numeric-answers.md) |
| 2026-10-11 | 用户确认每家公司唯一主Memo，save由Python确定性追加，重复报告幂等，保留旧条目/history | [当前Memo契约](technical-spec.md#10-独立交互agent数值回答与公司memo)、[教学38](38-company-memo-save.md) |
| 2026-10-12 | 用户要求整理spec与vibe coding；现行正文/单次变更/历史分开，验收先行，本地检查自动留证，常规开发不逐步审批 | [开发流程](development-workflow.md)、[本次变更](changes/20261012-spec-workflow.md) |

## 历史保留方式

[整理前快照](history/20261012-before-workflow/README.md)保留原README、AGENTS、PLAN、PROGRESS、技术规格、教学约定与出处。每份加历史提示，相对Markdown链接重定位；manifest记录原始与归档SHA-256。快照描述当时状态，不能以其中“未实施/未调用/禁止TS”等旧段落覆盖现行规则。

后续只为真正影响产品/架构/权限/兼容的决定新增短记录；纯代码细节写本次变更，不再复制成多份长期要求。
