# 阶段 7：DeepSeek 直接写作入口

## 1. 本阶段解决什么问题

前六阶段的研究草稿都由宿主（OpenCode）或人工编写。这一阶段新增一个小型 Python writer：从某个 run 的**已归档来源**构造受限上下文，直接调用 DeepSeek 生成既有的 `ResearchDraft`，再复用 `save-draft`/`validate` 保存与校验。它让写作环节不再依赖聊天会话，但**不自动 `finish`**——正式归档仍由用户显式执行；模型只能使用已有 `source_id`/`locator`，`ticker`/`as_of`/总分/完成状态仍由 Python 控制。

本阶段只接一个供应商（DeepSeek），不建通用 provider/插件框架，不做自主检索、工具循环、MCP 或 LangGraph。

## 2. 数据流与调用链

```text
write-draft RUN_ID --provider deepseek [--model M]
    │  读取 DEEPSEEK_API_KEY（缺失即退出，不发起请求）
    ▼
writer.write_draft()
    ├── 读 run 的已归档来源（source_json / text_path），构造受控上下文
    │     来源清单（source_id/kind/published_at/observed_after_as_of）+ 分段截取片段
    ├── run_steps 记 write_draft(running)：provider/model/input_hash
    ├── POST https://api.deepseek.com/chat/completions（response_format=json_object）
    ├── 解析 JSON → Python 覆写 ticker/as_of/schema_version → ResearchDraft 校验
    ├── 若仅 schema 校验失败，最多再发起一次结构修复请求并重新校验
    ├── research.save_draft()（合并 run 已挂来源，允许缺口，返回 issues）
    └── run_steps 记 succeeded/failed：template_version、usage、finish_reason、issue 数
    ▼
草稿写入 SQLite；不 finish；CLI 返回 {ok,data,issues}
```

## 3. 实际文件与入口

- `src/wonder_stockresearch/writer.py`（新）
  - `write_draft(root, run_id, *, provider="deepseek", model=None)`：唯一入口。
  - `_build_messages`：one-shot 回归路径最多读取 20 条来源；普通来源每条 ≤4000 字符，Provider 每条 ≤120000 字符，总计 ≤300000 字符，片段带 `locator_hint`。阶段 16 的默认 staged 路径改用按问题组有界的 EvidencePack，见 `docs/13-fixed-research-quality.md`。
  - `_request_deepseek`：一次 POST，连接 10s、读取 300s，`response_format={"type":"json_object"}`，`max_tokens` 默认 16000（`WONDER_DEEPSEEK_MAX_TOKENS`）；默认 `thinking=disabled`、`temperature=0.2`（`--thinking`/`--temperature` 或 `WONDER_DEEPSEEK_THINKING`/`WONDER_DEEPSEEK_TEMPERATURE` 覆盖）；4xx/5xx/网络错误转为 `WriterError`，**错误信息不含凭据**。
  - `_parse_draft`/`_validate_draft`：`json.loads` → 覆写 `ticker`/`as_of`/`schema_version` → `ResearchDraft.model_validate`；失败时保留受控的字段路径，供一次 schema 修复请求使用。
  - `_schema_repair_messages`：只允许修复结构类型和必填字段，不允许改变事实、数字、引用或身份字段。
- `src/wonder_stockresearch/storage.py`：`SCHEMA_VERSION=3`，`_MIGRATIONS` 新增 v2→v3 `ALTER TABLE run_steps ADD COLUMN metadata_json TEXT`；`insert_step`/`finish_step`/`list_steps` 支持 `model`/`metadata_json`。
- `src/wonder_stockresearch/research.py`：`_begin_step`/`_end_step` 增加 `model`/`metadata` 参数。
- `src/wonder_stockresearch/cli.py`：新增 `write-draft RUN_ID --provider deepseek [--model MODEL]`，捕获 `WriterError`。
- `tests/test_writer.py`（新，8 个测试）、`tests/test_run_steps.py`（v1→v3、v2→v3 迁移）。

Prompt 模板版本为 `deepseek-draft-v3`。`run_steps.metadata_json` 除原有 `usage`、`finish_reason` 和 issue 数外，还记录模型调用次数、是否触发 schema 修复、初次/最终校验状态及每次调用的受控 usage；不记录提示词、隐藏推理、认证头或大段原文。

### 完整问题定义与题意对齐

Writer 和报告渲染共用 `questions.py` 中 Q01–Q24 的完整问题文本，不再只给模型问题编号。Q03 要回答资本开支（金额、占收入比例、期间和口径），不能用利润率、ROE、净利润或 EPS 代替；Q04 要回答技术替代或产品变化的速度及其竞争影响，不能用 EPS、利润率或估值指标代替。缺少直接证据时，提示要求使用 `partial`/`missing` 并描述 gap。

题意指引可以减少编号错位，但不是独立的语义校验器；如果回答仍未切合问题，不能仅凭结构校验通过就认为内容正确。已有完成报告不重写。

### Gap 字段约束与受控修复

`Answer.gap` 只能是 `null` 或完整对象：

```json
{"missing":"缺少什么","where_to_find":"去哪里找","next_check":"下一步核对什么","impact":"对判断有什么影响"}
```

`status=answered` 必须使用 `gap=null`；`status=partial` 或 `missing` 必须提供四个非空字段。JSON 语法正确不代表符合 `ResearchDraft` schema，DeepSeek 的 JSON 模式仍由 Pydantic 做最终校验。

如果初次输出只发生 schema 校验错误，writer 会把受控的字段路径和原结构发送给 DeepSeek，请它只修复结构一次。Python 还会比较修复前后的非 `gap` 内容；如果陈述、数字、引用或结论被顺手改动，即使新 JSON 能通过 schema，也会拒绝保存。修复成功才保存草稿；修复失败仍返回 `invalid_schema`，不保存草稿，也不能绕过 `validate`/`finish`。非法 JSON、网络错误和认证错误不会进入这条 schema 修复路径。

## 4. 最小可运行示例与真实结果

```bash
cp .env.example .env          # 项目根；已 gitignore，填入 DEEPSEEK_API_KEY
# 也可以不用 .env，改成：export DEEPSEEK_API_KEY=...
uv run wonder-research start HK.00728
uv run wonder-research collect "$RUN" --kind filings
uv run wonder-research write-draft "$RUN" --provider deepseek
uv run wonder-research validate "$RUN"     # 人工检查缺口与引用
# 满意后再显式 finish；writer 不会自动完成
```

凭据来源优先级：已存在的 shell 环境变量 > 项目根 `.env`。`.env` 由 `envfile.load_env_file` 读取（键值解析、不覆盖、不打印），`cli.main` 与 `writer.write_draft` 都会尝试加载；`.env` 永不提交，模板为 `.env.example`。

本阶段实施后的实际观察：

```text
--help            → 新增 write-draft
项目 DB            → 触发迁移后 user_version 2 → 3，run_steps 出现 metadata_json 列
未设置 key 时      → exit 2，issues=[missing_api_key]，不发起请求、不写 run_steps
```

真实 DeepSeek API smoke：**已单独运行一次**（用户在本机 `.env` 配置 `DEEPSEEK_API_KEY` 后，对 HK.00728 一个含 5 条来源的 draft run）：

```text
write-draft → exit 0，ok=true，issues=[]，saved=true
              model=deepseek-flash，thinking=disabled，temperature=0.2
              usage={prompt_tokens: 6337, completion_tokens: 9534, total_tokens: 15871}
validate    → exit 0，issues=[]，answer_count=24，quality_status=evidence_incomplete
```

首次尝试（默认 thinking 开启、未设温度）出现过两类失败，已修正并被测试覆盖：thinking 的推理 token 挤占 `max_tokens` 导致输出截断（`invalid_json`），以及模型数字缺少 `period`（`invalid_schema`）。端点/模型按官方文档核对：base `https://api.deepseek.com`、`/chat/completions`、JSON 模式、模型 `deepseek-flash`/`deepseek-v4-pro`。

## 5. 为什么这样实现

- **Python 掌握边界**：`ticker`/`as_of`/`schema_version` 由 Python 覆写，模型不能改研究截止时点或身份；引用不存在、时间不可用、评分越界等仍由既有 `validate_packet` 拒绝。
- **上限受控**：来源条数与字符数封顶，避免把整本 PDF 塞进上下文；片段来自 `text_path`，与 `source-read` 同源。
- **凭据最小暴露**：只从 `DEEPSEEK_API_KEY` 读取（优先 shell 环境变量，其次项目根 `.env`，该文件已 gitignore）；请求头不落库、不入日志、不进错误信息；测试断言 metadata/错误里不含 key。
- **温度与 thinking**：DeepSeek 官方文档说明 `deepseek-flash` 默认开启 thinking，且 **thinking 模式下 `temperature` 不生效**、推理 token 会计入输出预算。结构化写作因此默认 `thinking=disabled` + `temperature=0.2`（低温度减少编造、提高 schema/引用遵循度）；需要更强推理时可 `--thinking enabled`，但此时温度无效、输出预算要留足。
- **上限受控**：正常生成最多一次；只有 `ResearchDraft` schema 校验失败时才允许一次结构修复，不做事实重写、无限循环或自动 finish。
- **可审计**：`run_steps` 记录 provider/model/input_hash/template_version/usage，便于复现与预算核对，但不保存提示词或隐藏推理。
- **迁移显式**：v2→v3 用 `ALTER TABLE` 加列，旧库与旧步骤保留。

## 6. 测试覆盖与不能证明的事

`tests/test_writer.py` 覆盖：成功生成并保存、只保存不 finish、run_steps 记录 provider/model/input_hash/template/usage 且不含 key；缺 key 在请求前失败且不写步骤；网络失败记 failed 且不保存草稿；非法 JSON / schema 不符 / 空内容被拒；`gap` 字符串、数组和不完整对象的一次修复；修复失败不保存草稿；伪造 `source_id` 保存后由 `validate` 报 `unknown_source`；HTTP 500 与 401 不泄漏 key；上下文包含归档片段与 source_id；CLI 成功与缺 key 契约。`tests/test_run_steps.py` 覆盖 v1→v3 与 v2→v3 迁移。

这些测试用 fake 传输，**不能**证明：DeepSeek 实际回复质量、JSON 模式不返回空内容、真实限额/延迟、模型是否遵守缺口规则，或公司结论正确。真实 API 行为需在配置好凭据后单独 smoke。

## 7. 小练习

1. 配好 `DEEPSEEK_API_KEY` 后对同一 run 跑 `write-draft`，检查 `status RUN` 的 `write_draft` 步骤 metadata 里的 `usage` 与 `template_version`。
2. 把 `WONDER_DEEPSEEK_MAX_TOKENS` 调到很小，观察模型输出被截断后如何以 `invalid_json`/`invalid_schema` 失败且不生成完成报告。
3. 伪造一个不存在的 `source_id`（改 fake 或手写草稿）后 `validate`，确认 `unknown_source` 被拒绝、run 仍为 draft。

## 8. 已知限制与下一阶段

- 单供应商；只对 schema 错误做一次结构修复，不做事实重写、工具调用或检索；不自动 `finish`。
- 上下文仅来自本地已归档来源；未归档材料不会被模型看到。
- 真实 API smoke 尚未运行（本环境无凭据）。
- 后续可选阶段（LangGraph 等编排）只有在真实运行证明有多轮补证据、跨天恢复、并行或动态工具选择等持续痛点时才评估；本版本不再扩展。
