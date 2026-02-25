---
name: form_reporter
description: "表单报告（人类应用示例）：把收集结果与证据指针写入 workspace。"
metadata:
  short-description: "Report：file_write 写 submission.json/report.md。"
---

# form_reporter（app / Report）

## 目标

把结果与证据落盘到 workspace：
- `submission.json`
- `report.md`（摘要 + 产物清单 + wal_locator 提示）

## 必须使用的工具

- `file_write`

