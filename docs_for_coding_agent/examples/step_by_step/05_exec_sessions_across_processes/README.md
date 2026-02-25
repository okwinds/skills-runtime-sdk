# 05_exec_sessions_across_processes（exec sessions 跨进程复用）

目标：
- 演示 `exec_command/write_stdin` 由 workspace runtime 托管，`session_id` 可跨多次 CLI 调用复用
- 该能力用于“PTY 常驻会话”（例如 REPL、交互式脚本、长命令）

运行（离线，可回归）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/step_by_step/05_exec_sessions_across_processes/run.py --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: step_by_step_05`
