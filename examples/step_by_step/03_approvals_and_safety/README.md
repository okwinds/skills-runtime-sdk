# 03_approvals_and_safety（审批与安全策略）

目标：
- 演示 `safety.mode=ask` 下，危险工具（例如 `file_write`）会触发 approvals
- 演示 `approved_for_session` 的缓存语义（同一 approval_key 第二次命中 cached）
- 演示 denied 分支（工具不会执行，tool result 为 permission）

运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/step_by_step/03_approvals_and_safety/run.py --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: step_by_step_03`

