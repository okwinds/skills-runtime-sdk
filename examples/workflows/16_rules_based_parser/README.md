# 16_rules_based_parser（规则驱动的结构化解析器 / Skills-First）

本示例演示一个常见“业务落地形态”：

- 业务用自然语言描述解析规则（示例中内置一段规则文本）
- 系统产出一个**可执行的结构化 plan**（`plan.json`）
- 用确定性方式执行 plan 并落盘结果（`result.json`）
- 全过程通过 builtin tools 的 `file_write` 落盘，走 approvals，WAL 可审计

关键约束：**skills-first** —— 任务文本包含 `$[examples:workflow].rules_parser`，WAL 中必须出现 `skill_injected`。

## 如何运行（离线）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/16_rules_based_parser/run.py --workspace-root /tmp/srsdk-wf16
```

预期：

- 运行成功退出（exit=0）
- stdout 含：`EXAMPLE_OK: workflows_16`
- workspace 下生成：`plan.json`、`result.json`、`report.md`
- WAL（`events.jsonl`）中应出现：
  - `skill_injected`（mention 为 `$[examples:workflow].rules_parser`）
  - `tool_call_finished`（tool 为 `file_write`，ok=true）
  - `approval_requested/approval_decided`（因为 safety.mode=ask）

