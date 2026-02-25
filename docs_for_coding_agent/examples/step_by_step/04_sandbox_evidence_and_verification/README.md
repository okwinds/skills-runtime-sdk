# 04_sandbox_evidence_and_verification（sandbox 证据字段 + 真实验证入口）

目标：
- 演示 `shell_exec` 的 `data.sandbox` 证据字段（requested/effective/adapter/active）
- 演示 fail-closed：当 `effective=restricted` 且缺少 adapter 时，返回 `sandbox_denied`
- 给出“如何验证真沙箱生效”的可复现入口（脚本 + 证据字段）

运行（离线，可回归）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/step_by_step/04_sandbox_evidence_and_verification/run.py --workspace-root /tmp/srsdk-demo
```

真实沙箱验证（可选）：

```bash
bash scripts/integration/os_sandbox_restriction_demo.sh
```

并检查 `tool_call_finished.result.data.sandbox.active/adapter/effective`。

预期输出（关键标记）：
- `EXAMPLE_OK: step_by_step_04`
