# 22_chatops_incident_triage（ChatOps 排障：澄清 → 计划 → Runbook/Report）

本示例演示一个“ChatOps 排障助手”的最小可复刻骨架：

- 输入：workspace 生成 `incident.log`（模拟线上告警/故障日志）
- 澄清：`request_user_input` 向人类提 1-2 个关键问题（离线：scripted HumanIOProvider 注入答案）
- 同步：`update_plan` 把推进过程结构化可见（WAL 事件 `plan_updated`）
- 产出：`file_write` 落盘 `runbook.md` + `report.md`（写操作走 approvals）
- 证据：WAL 断言 `human_request/human_response`、`plan_updated`、`tool_call_finished`

## 运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/22_chatops_incident_triage/run.py --workspace-root /tmp/srsdk-wf22
```

## 产物

- `incident.log`：输入（示例生成）
- `runbook.md`：建议处置步骤（可迁移模板）
- `report.md`：包含 `run_id`、`events_path` 与关键证据摘要

