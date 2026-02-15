# 01_offline_minimal_run（离线最小 run）

目标：
- 演示如何用 `FakeChatBackend` 在离线环境跑通 `Agent.run(...)`
- 获取 `final_output` 与 `events_path`（WAL 产物位置）

运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/step_by_step/01_offline_minimal_run/run.py --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: step_by_step_01`

