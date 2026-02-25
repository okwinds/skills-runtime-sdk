---
name: ci_qa_reporter
description: "CI 回归与报告（人类应用示例）：回归验证并输出 report.md。"
metadata:
  short-description: "QA+Report：shell_exec + file_write。"
---

# ci_qa_reporter（app / QA+Report）

## 目标

- 使用 `shell_exec` 进行最小回归（例如 `pytest -q`）
- 使用 `file_write` 输出 `report.md`（问题/修复/验证命令）

