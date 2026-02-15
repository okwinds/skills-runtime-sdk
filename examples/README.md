# Examples（全能力示例库）

本目录提供 **可复制运行** 的示例集合，目标是：
- 覆盖 Skills Runtime SDK 的核心能力点（见 `docs_for_coding_agent/capability-inventory.md`）
- 默认离线可运行（fake backend / stub），用于回归与教学
- 需要真模型/联网的示例必须显式 opt-in（不进入离线门禁）

推荐阅读顺序：
1. `examples/step_by_step/`：从 0→1 跑通离线 Agent Loop 与 tool_calls
2. `examples/tools/`：工具协议与注册表（ToolRegistry）
3. `examples/skills/`：skills preflight/scan 的最小链路
4. `examples/state/`：WAL replay / fork
5. `examples/workflows/`：项目级示范（Skills-First 组合编排，多 agent 流水线）

离线 smoke tests（门禁）：
- `pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py`
