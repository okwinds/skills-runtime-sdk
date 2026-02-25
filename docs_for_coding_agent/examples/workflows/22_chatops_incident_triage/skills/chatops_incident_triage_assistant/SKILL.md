---
name: chatops_incident_triage_assistant
description: "读取 incident.log，澄清关键问题（request_user_input），更新计划（update_plan），输出 runbook.md/report.md。"
metadata:
  short-description: "ChatOps 排障：Human I/O 澄清 + Plan 同步 + 产物落盘。"
---

# chatops_incident_triage_assistant（workflow / ChatOps Incident Triage）

## 目标

把常见的“故障排障”过程做成可复刻、可审计的最小 workflow：
- 先读输入（`incident.log`）
- 再澄清关键问题（`request_user_input`）
- 再同步推进计划（`update_plan`）
- 最后落盘 runbook/report（`file_write`，走 approvals）

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].chatops_incident_triage_assistant`。
- workspace 内存在 `incident.log`，包含故障摘要/错误信息。

## 必须使用的工具

- `read_file`：读取 `incident.log`
- `request_user_input`：澄清 1-2 个关键问题（无 human_io provider 时必须 fail-closed）
- `update_plan`：同步推进（WAL：`plan_updated`）
- `file_write`：落盘 `runbook.md` / `report.md`（写操作，通常需要 approvals）

## 约束

- 默认离线：不访问外网，不依赖真实 key。
- 输出必须可复用：`runbook.md` 以通用模板表达，不写死具体供应商/业务名词。

