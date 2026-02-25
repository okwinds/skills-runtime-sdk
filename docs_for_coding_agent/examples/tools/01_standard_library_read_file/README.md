# 01_standard_library_read_file（ToolRegistry + read_file）

目标：
- 不经过 Agent Loop，直接用 `ToolRegistry.dispatch(...)` 执行 builtin tool：`read_file`
- 演示 workspace_root 下路径约束

运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/tools/01_standard_library_read_file/run.py --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: tools_read_file`
