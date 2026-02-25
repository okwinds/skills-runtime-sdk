# 09_branching_router_workflow（路由分支 / Skills-First）

本示例演示一个非常常见的“项目级编排”模式：**先路由（router）→ 再执行（worker）→ 再汇总（report）**。

核心约束（Skills-First）：
- 每个角色能力必须由 `skills/*/SKILL.md` 定义；
- 任务文本显式包含 mention，触发 `skill_injected` 证据事件；
- 产物可检查：`route.json`、`outputs/*`、`report.md`；
- 默认离线可回归（Fake backend + scripted approvals）。

## 运行方式（离线）

```bash
python3 docs_for_coding_agent/examples/workflows/09_branching_router_workflow/run.py --workspace-root /tmp/srsdk-demo
```

选择分支（默认 A）：

```bash
python3 docs_for_coding_agent/examples/workflows/09_branching_router_workflow/run.py --workspace-root /tmp/srsdk-demo --route B
```

## 你应该看到什么

- `task_input.json`：输入（本例用来决定路由）
- `route.json`：router 的路由决策（A / B）
- `outputs/path_a.md` 或 `outputs/path_b.md`：分支产物
- `report.md`：汇总报告（包含各步骤的 `wal_locator` 指针）
- `.skills_runtime_sdk/runs/<run_id>/events.jsonl`：WAL（可审计证据链）

## 对应 skills

- `skills/router/SKILL.md`
- `skills/path_a_worker/SKILL.md`
- `skills/path_b_worker/SKILL.md`
- `skills/reporter/SKILL.md`
