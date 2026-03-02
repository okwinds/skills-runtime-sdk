# Examples（面向人类的应用示例）

本目录提供 **面向人类读者** 的示例集合，目标是：
- 跑起来像“小应用”：有交互、有过程感、有产物
- 默认离线可运行（fake backend / stub），用于回归与演示
- 提供真模型（OpenAICompatible）运行方式（需要你本地配置 key；不进入离线门禁）

如果你要找“面向编码智能体的能力覆盖示例库”（step_by_step/tools/skills/state），请看：
- `docs_for_coding_agent/examples/`

当前人类示例入口：
- `examples/apps/`：应用示例（推荐入口；离线可回归 + 真模型可跑）
- `examples/studio/`：Studio 相关示例素材（如有）

离线 smoke tests（门禁，覆盖代表性示例）：
- `pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py`

## 验收标记（离线回归观察点）

离线 smoke tests 的约定是：每个被覆盖的 app 运行后 stdout 必须包含对应的 `EXAMPLE_OK:` 标记，例如：
- `EXAMPLE_OK: app_form_interview_pro`
- `EXAMPLE_OK: app_fastapi_sse_gateway_pro`
