---
name: repo_reporter
description: "将流水线结果写入 workspace 报告文件（workflow 示例：Report 角色）。"
metadata:
  short-description: "Report：file_write 生成 report.md（包含证据指针）。"
---

# repo_reporter（workflow / Report）

## 目标

生成 `report.md`，包含摘要与 WAL 证据指针（events_path）。

## 必须使用的工具

- `file_write`

