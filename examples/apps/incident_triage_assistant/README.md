# incident_triage_assistant（排障助手 / Skills-First）

目标：模拟一次真实 oncall 排障的最小闭环：
- 读取 `incident.log`（`read_file`）
- 结构化澄清（`request_user_input`）
- 计划同步（`update_plan`）
- 输出 runbook 与报告（`file_write`）

## 1) 离线运行（默认，用于回归）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/incident_triage_assistant/run.py --workspace-root /tmp/srsdk-app-incident --mode offline
```

预期：
- stdout 含：`EXAMPLE_OK: app_incident_triage_assistant`
- workspace 下生成：`incident.log`、`runbook.md`、`report.md`

## 2) 真模型运行（OpenAICompatible）

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"      # 可选
export SRS_MODEL_PLANNER="gpt-4o-mini"                  # 可选
export SRS_MODEL_EXECUTOR="gpt-4o-mini"                 # 可选

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/incident_triage_assistant/run.py --workspace-root /tmp/srsdk-app-incident --mode real
```

