# 01_wal_replay_and_fork（WAL replay + fork）

目标：
- 产生一段 WAL（包含 tool_call_finished）
- 选取一个事件点 fork 出新 run
- 用 `resume_strategy=replay` 在 fork run 中重建 tool message

运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/state/01_wal_replay_and_fork/run.py --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: state_wal_replay_fork`

