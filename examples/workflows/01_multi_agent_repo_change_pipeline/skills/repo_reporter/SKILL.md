---
name: repo_reporter
description: "将流水线结果写入 workspace 报告文件（workflow 示例：Report 角色）。"
metadata:
  short-description: "Report：file_write 生成 report.md，包含摘要与证据指针。"
---

# repo_reporter（workflow / Report）

## 目标

把本次流水线的关键结果沉淀为一个可读报告（`report.md`），便于：
- 人类审阅
- 复盘与审计（WAL events_path 指针）

## 输入约定

- 你的任务文本中会包含 mention：`$[examples:workflow].repo_reporter`。
- 上游会提供：各步骤摘要、关键证据（例如 events_path、QA stdout 关键字）。

## 必须使用的工具

- `file_write`：写入 `report.md`（写操作，通常需要 approvals）

## 报告内容建议

请至少包含：
1. 本次目标与范围（Goal/Scope）
2. 每个角色步骤的摘要（Analyze/Patch/QA）
3. 证据指针（每个子 agent 的 `events_path`）
4. 下一步建议（可选）

