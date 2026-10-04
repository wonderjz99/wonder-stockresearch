---
name: company-analysis-method
description: 公司分析方法与证据纪律。用于理解 Q01–Q24、公司门闸、M1–M6、反方论证、缺口和 as_of 规则；它不是 CLI 操作流程。
---

# 公司分析方法

这是本项目的分析方法 Skill，不是命令操作 Skill。具体问题、门闸、护城河维度、估值分离和证据规则以 `docs/research-spec.md` 为准。

## 边界

- 标准公司研究覆盖 Q01–Q24、G1/G2/G3/G5/G6/G7、M1–M6。
- Q25–Q28/G4 属于估值，不进入公司评分。
- 公司判断必须能追溯到 source_id 和真实 locator。
- `as_of` 之后取得或无法证明当时可用的资料不能作为核心 supports。
- 资料不足时使用 partial/missing/insufficient/null，不编造数字。
- 价格、估值、分析师评级不能直接改变公司门闸或护城河评分。

## 与其他 Skill 的关系

- `company-research`：固定 CLI 流程，负责 start、collect、source-read、write-draft、validate、finish。
- `interactive-research-agent`：开放问题的临时分析和 Memo 保存规则。

交互 Agent 可以读取本方法和正式报告，但不会因为一次临时回答而修改正式报告。若要更新标准研究，重新启动固定 ResearchRun。
