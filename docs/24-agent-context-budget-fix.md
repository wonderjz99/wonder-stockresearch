# 首轮 Agent 预算误拦截修复（2026-10-08）

## 1. 问题与前一阶段的关系

用户执行 `uv run wonder-research chat --text "中国移动最近三年毛利率平均值"`，Router 修复一次 JSON 后识别为 interactive_question，Agent 第 1 轮即返回 agent_budget_exhausted。

这是 19D.1 请求前预算检查与旧上下文策略没有配合的回归。短问题会自动带入规则、Tool schema、Skill、来源目录和历史记忆。旧检查按“1 字符约等于 1 token”计数，而旧压缩要到 28,000 字符才触发。该请求重建得到 system 6,392 字符、上下文约 17,351 字符，处于“超过 12,000 预算，但未达到压缩阈值”的区间。

主库中该失败 Agent Session 为 budget_exhausted、iteration=1、used_tokens=0，符合“Agent HTTP 请求前被拒绝”的路径；不能据此说前面的 Router 调用没有费用。Router JSON 修复是独立的协议校验流程，修复成功后已经继续；现有安全日志不足以确认第一次输出具体哪个字段错误。

## 2. 调用图

```text
chat → Router 校验/最多一次修复 → Skill 加载 → Agent
  → 精简首轮上下文 → 按剩余预算压缩
  → 持久化请求预算预留 → API 前估算/限制输出
  → 按 usage 结算 → 工具/最终回答
```

## 3. 实际实现

- `agent_memory.estimate_input_tokens`：ASCII 按 0.4、非 ASCII 按 1.0 加每条消息 32 的开销进行工程估算；不再逐字符视为 token。它不是官方 tokenizer，不保证严格上界。实际累计费用仍以 API usage 为准，超预算回答仍拒绝。
- `agent._initial_context`：首轮来源目录省略 metadata；最多两条历史记忆，各 1,200 字符、三个来源索引，附 source_refs_omitted。原始记录不删，完整来源和报告仍通过受控工具读取。
- `agent_memory.compress_messages(..., max_input_tokens=...)`：保留旧字符阈值，同时按剩余 Turn 预算减去输出预留触发压缩。system/Tool schema 和完整问题及用户补充保留；先缩小旧观察等工作记忆投影，再按需要省略最近交互。能容纳的最近工具结果完整保留，避免直接截去尾部数字/locator；持久化 Scratchpad 与证据回执不删。
- `agent._api_call_for_root`：采用同一估算限制 max_tokens；仍放不下时说明剩余额度和估算输入，不发 HTTP、消耗记为 0。未知 usage 保留预留的规则不变。

没有增加预算默认值，没有修改正式研究、评分、Router 行为或数据库 schema。没有引入 tokenizer 依赖或框架。

## 4. 最小运行与恢复

```sh
./.venv/bin/python -m pytest -q tests/test_agent_context_budget.py
```

上述三个测试分别通过实际项目 Skill 与两份大型历史记忆进入 chat→Agent→API 入口、验证低于旧字符阈值时的预算压缩、验证真正不足时 HTTP 零调用且零消耗。

修复后可重新运行原命令，创建新的 Session/Turn：

```sh
uv run wonder-research chat --text "中国移动最近三年毛利率平均值"
```

这是会调用已配置真实模型的用户操作示例；本次没有运行。原 Turn 已是 budget_exhausted，按既有终态规则不能 ask-retry；不手动篡改数据库清零预算。新请求仍可能因真实多轮累计用量耗尽预算，不能保证任何问题都在默认额度内完成。

## 5. 为什么这样修

字符阈值管理上下文体积，Turn token 预算管理累计消耗，两者单位不同。仅提高预算默认值会掩盖重复注入，降低阈值也无法处理每轮递减的剩余额度。因此统一估算函数、按当前额度提前压缩，并减少不必要的历史投影。

权限和用户问题不静默截断。确实装不下时停止并解释原因，比发出超大请求后才发现预算不够更可检查。估算仍有误差；需要精确 token 统计时应另行验证模型对应的 tokenizer，而不是把本次启发式计数称为精确值。

## 6. 实际测试与副本验证

- `./.venv/bin/python -m pytest -q`：216 passed，2.47 秒。
- `./.venv/bin/ruff check src tests`：All checks passed。
- 在主库只读备份出的临时副本中，使用原请求及 Skill 授权，经过真实 API 调用入口、替换 HTTP 为离线响应：首轮 14,221 字符、估算输入 7,430 tokens、max_tokens=4,570；第二轮 14,283 字符、估算输入 7,496、max_tokens=3,904。默认 12,000 预算下两次占位响应后 succeeded。
- 副本复现不是毛利率计算验收；离线响应只给占位结论与缺口，不能作为公司研究证据。真实网络调用为 0，主库文件哈希未变，临时副本自动清理。摘要位于 `data/audits/20261008-context-budget/offline-replay.json`。

旧 scripted model 测试直接注入 decide，绕过 `_api_call_for_root` 的前置估算，因而漏掉这次回归。新增测试通过 mocked HTTP 保留预算前置检查，仍不访问外部服务。

## 7. 小练习

给 `test_compression_uses_remaining_budget_below_old_character_threshold` 增加一条带尾部数值的工具结果。额度允许时完整结果应保留；降低 max_input_tokens 后先省略旧观察，再省略无法容纳的近期内容，system 与原问题始终保留。压缩不是对证据真实性的证明。

## 8. 限制与后续

未调用真实 DeepSeek/OpenD，未验证真实毛利率答案或模型的多轮成本分布。只修复当前故障及首轮重复注入；中文检索相关性、Turn 工作记忆隔离、共享 FTS/备份仍按原审计分阶段推进。此次没有改变既有失败 Turn 或正式报告。
