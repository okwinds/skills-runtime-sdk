# 04_map_reduce_parallel_subagents（总分总 + 并行子任务 / Skills-First）

本示例演示一个“总分总”的 agent 编排形态，并强调 **skill-first**：

1. **总（Planner）**：先总规划，把任务拆成互不依赖的子任务（用 `update_plan` 记录进度），并落盘 `subtasks.json`（可审计）。
2. **分（Subagents 并行）**：多个子 agent **并行执行**，每个子任务对应一个 Skill，并各自产出独立产物文件（互不影响）。
3. **总（Aggregator）**：汇总所有子任务结果，生成 `report.md`（包含各子任务 wal_locator 证据指针）。

约束：
- 离线可回归：Fake backend + scripted approvals
- 每个角色都必须有 Skill，并通过任务文本中的 mention 触发 `skill_injected`

## 如何运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/04_map_reduce_parallel_subagents/run.py --workspace-root /tmp/srsdk-wf04
```

## 你将看到

- `subtasks.json`：总规划拆解出的子任务清单（可迁移到真实项目）
- `outputs/*.md`：子任务产物（并行生成）
- `report.md`：最终汇总报告（含 evidence 指针）
- `EXAMPLE_OK: workflows_04`：stdout 稳定标记（用于离线门禁）

