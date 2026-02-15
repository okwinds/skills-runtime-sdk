# 02_offline_tool_call_read_file（离线 tool_calls：read_file）

目标：
- 演示 tool_calls 的最小闭环：LLM 提出 `read_file` → 工具执行 → 回注 → LLM 完成
- 不依赖真实模型/外网

运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/step_by_step/02_offline_tool_call_read_file/run.py --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: step_by_step_02`

