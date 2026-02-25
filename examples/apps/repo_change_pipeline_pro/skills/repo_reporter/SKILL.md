---
name: repo_reporter
description: "Repo 流水线：输出补丁与报告。"
metadata:
  short-description: "file_write artifacts"
---

# repo_reporter（app / Report）

## 目标

- 输出 `patch.diff` 与 `report.md`
- 报告应包含：问题、修复、验证命令、产物清单

## 必须使用的工具

- `file_write`

