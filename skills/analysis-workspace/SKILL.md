---
name: analysis-workspace
description: 查询公司基本信息和财务字段、补取富途资料，用 SQL 或受限函数计算。
---

# 临时分析

1. company_latest 范围先 query_company_data（默认 fields）看字段与可用期间，再用 SQL 选最小输入；facts 适合少量基本信息/单期值。其他范围用 query_archived_financials。字段目录不能引用。has_more/context_truncated/records_omitted 表示省略，不能推断缺数据。
2. 缺原始字段/期间才 fetch_financial_data：profile 公司概况，statements 三表/关键指标，operational_efficiency 经营效率，revenue_breakdown 营收构成。接口失败、解析失败与未采集须区分。已有输入直接计算，固定公式不限制指标。
3. 目录已返回 sql_tables，不必再 describe_analysis_schema。SELECT 保留 fact_id 获得引用；value_decimal 是文本。按 field_key 区分 data/yoy/qoq，核对期间类型、维度、币种、单位，不混算。未知单位/倍率不可换算；同一字段可算原始值平均并说明单位未知。
4. 取得 analysis_id 即可 run_analysis_function；函数收到完整 SQL 行，不受预览省略影响。取近期序列用 ORDER BY period DESC LIMIT 所需条数，不能心算。函数 def analyze(rows) 返回 JSON 对象；支持 for/if、推导式、下标和 sum/len/float/int/round/min/max/sorted，禁止 import、属性访问、文件、网络、环境和 shell。例如（输入已筛选）：

```python
def analyze(rows):
    return {'mean': sum([float(row['value_decimal']) for row in rows]) / len(rows), 'count': len(rows)}
```

补取后新查询可见新资料，旧产物不变；company_latest 的新 Turn 复用归档，重试固定版本。不要重复成功调用，最终引用须来自本 Turn 工具定位。正式研究不变，明确 memo-save 才保存 Memo。
