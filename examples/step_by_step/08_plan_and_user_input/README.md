# 08：Plan + Human I/O（update_plan / request_user_input）

本示例演示两类“协作原语”：

1) `update_plan`：把任务计划（step/status）以结构化方式更新，并（在有 WAL 时）发出 `plan_updated` 事件。
2) `request_user_input`：向用户请求结构化输入（多题/多选），并返回 answers；无 `human_io` 时必须 fail-closed（`human_required`）。

本示例用 **tools CLI** 演示（离线，可回归），并通过 `--answers-json` 提供离线答案，避免交互阻塞。

---

## 如何运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/step_by_step/08_plan_and_user_input/run.py --workspace-root /tmp/srsdk-demo
```

预期输出包含：
- `EXAMPLE_OK: step_by_step_08`

---

## 你应该观察什么

- `update_plan`：返回 `data.plan[]`（含 status），且最多一个 `in_progress`
- `request_user_input`：返回 `data.answers[]`（按 question_id 对应）

如果你希望验证“事件落盘”的证据链，请在 agent 的 `run_stream` 模式下运行（启用 WAL），并在
`<workspace_root>/.skills_runtime_sdk/runs/<run_id>/events.jsonl` 中观察：
- `plan_updated`
- `human_request` / `human_response`

