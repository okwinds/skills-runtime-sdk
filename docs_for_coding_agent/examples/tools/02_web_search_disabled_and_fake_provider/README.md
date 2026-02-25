# Tools：web_search（默认关闭 + Fake Provider 离线启用）

`web_search` 是 Codex parity 工具之一，但本 SDK **默认 fail-closed**：
- tools CLI 不会注入联网 provider（避免“隐式联网”与不可回归）
- 你需要在产品侧显式注入 provider（并做联网治理：启用策略、限流、审计、脱敏等）

本示例演示两件事：
1) 默认关闭时的错误语义（`disabled` + `error_kind=validation`）
2) 离线 fake provider 的最小启用方式（不依赖外网）

---

## 如何运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/tools/02_web_search_disabled_and_fake_provider/run.py --workspace-root /tmp/srsdk-demo
```

预期输出包含：
- `EXAMPLE_OK: tools_02_web_search`
