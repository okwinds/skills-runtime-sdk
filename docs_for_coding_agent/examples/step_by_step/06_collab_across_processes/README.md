# 06_collab_across_processes（collab primitives 跨进程复用）

目标：
- 演示 `spawn_agent/send_input/wait/close_agent` 由 workspace runtime 托管
- child agent id 可跨多次 CLI 调用复用

运行（离线，可回归）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/step_by_step/06_collab_across_processes/run.py --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: step_by_step_06`
