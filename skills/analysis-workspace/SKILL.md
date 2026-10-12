---
name: analysis-workspace
description: 查询公司基本信息和财务字段、补取富途资料，用 SQL 或受限函数计算。
---

# 临时分析

1. company_latest 中指标位置未知时 query_company_data(mode=fields, query=指标名)，省略 dataset 跨授权数据集找字段。已知 field_keys/用户限定 dataset 可直接查。确认原值/同比和期间类型，再用 SQL 选输入；少量值可用 facts，其他范围 query_archived_financials。目录不能引用；periods 是五期样本，完整期间查 facts/SQL；按 next_offset 续页。
2. 仅缺原始字段/期间才 fetch_financial_data；接口失败、解析失败、未采集分开。已有输入直接算，固定计算器没有指标不等于无数据。
3. 目录已有 sql_tables；SELECT 保留 fact_id，value_decimal 是文本。按 field_key 区分 data/yoy/qoq，核对期间、维度、单位/倍率。未知单位不补 %，原值平均说明未知。
4. 取得 analysis_id 后函数接收完整 SQL 行，预览省略不影响计算。ORDER BY period DESC LIMIT 取近期，不心算。def analyze(rows) 返回 JSON 对象；允许函数和排序 Lambda 边界见工具说明，禁止 import、属性/方法、文件/网络/shell。例如（SQL 已含 unit）：

```python
def analyze(rows):
    samples = [{'period': r['period'], 'value': float(r['value_decimal']), 'unit': r['unit']} for r in sorted(rows, key=lambda r: r['period'])]
    return {'samples': samples, 'mean': sum([r['value'] for r in samples]) / len(samples)}
```

工作清单给出已取得输入和当前错误。语法拒绝不损坏 SQL 输入；按提示纠正，不原样重试。无工具收尾时未完成写 gaps。补取不改旧产物，重试固定版本，引用来自本 Turn；正式研究不变，仅明确 memo-save 保存 Memo。
