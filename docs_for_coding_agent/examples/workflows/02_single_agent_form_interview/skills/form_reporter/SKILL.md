---
name: form_reporter
description: "将采集结果落盘为可复用产物（例如 JSON），并保留证据链（workflow 示例：Persist 角色）。"
metadata:
  short-description: "Persist：用 file_write 写 submission.json。"
---

# form_reporter（workflow / Persist）

## 目标

将结构化 answers 作为产物写入 workspace（例如 `submission.json`），便于：
- 业务流程继续处理
- 审计与回放（WAL 证据 + 可读文件）

## 必须使用的工具

- `file_write`：写入 `submission.json`（写操作，通常需要 approvals）

