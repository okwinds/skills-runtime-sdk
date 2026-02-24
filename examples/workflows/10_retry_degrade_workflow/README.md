# 10_retry_degrade_workflow（重试→降级→报告 / Skills-First）

本示例演示一个“真实项目里很常见”的失败处理模式：

1) 先定义重试预算与策略（可审计）
2) 执行若干次 attempt（失败也要留下证据）
3) 达到预算后走降级（degrade）路径
4) 汇总生成报告（report）

核心约束（Skills-First）：
- 每个角色能力必须由 `skills/*/SKILL.md` 定义；
- 任务文本显式包含 mention，触发 `skill_injected` 证据事件；
- 默认离线可回归（Fake backend + scripted approvals）。

## 运行方式（离线）

```bash
python3 examples/workflows/10_retry_degrade_workflow/run.py --workspace-root /tmp/srsdk-demo
```

## 你应该看到什么

- `retry_plan.json`：重试/降级策略（由 controller 生成）
- `outputs/fallback.md`：降级产物（当 attempt 失败达到预算）
- `report.md`：汇总报告（包含每次 attempt 的 exit_code 与 `wal_locator` 指针）

## 对应 skills

- `skills/retry_controller/SKILL.md`
- `skills/attempt_worker/SKILL.md`
- `skills/degrade_worker/SKILL.md`
- `skills/reporter/SKILL.md`

