# 11_collab_parallel_subagents_workflow（Collab 原语：spawn/wait/send_input / Skills-First）

本示例演示“总分总 + 并行子 agent”的另一种实现方式：不靠外部线程池，而是让 **主 agent 在自己的 tool_calls 中直接使用 collab 工具**：

- `spawn_agent`：生成子 agent
- `send_input`：给子 agent 追加输入（inbox）
- `wait`：等待子 agent 完成并拿到 final_output

注意：为了让离线回归可重复，本示例使用了一个 **示例专用的确定性 CollabManager**：
- 子 agent id 固定为 `sub1/sub2/sub3`（便于 Fake backend 在 `wait` 中硬编码 ids）
- 子 agent 的真正工作仍然是用本 SDK 的 `Agent.run(...)`（技能注入、WAL 证据链都齐全）

## 运行方式（离线）

```bash
python3 docs_for_coding_agent/examples/workflows/11_collab_parallel_subagents_workflow/run.py --workspace-root /tmp/srsdk-demo
```

## 你应该看到什么

- `outputs/research.md` / `outputs/design.md` / `outputs/risks.md`：并行子 agent 的独立产物
- `report.md`：汇总报告（包含 master + 子 agent 的 `wal_locator` 指针）
- `.skills_runtime_sdk/runs/<run_id>/events.jsonl`：每个 agent 的 WAL 证据链

## 对应 skills

- `skills/master_collab_planner/SKILL.md`
- `skills/subagent_worker_research/SKILL.md`
- `skills/subagent_worker_design/SKILL.md`
- `skills/subagent_worker_risk/SKILL.md`
- `skills/aggregator/SKILL.md`
