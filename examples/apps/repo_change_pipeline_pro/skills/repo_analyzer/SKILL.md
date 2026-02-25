---
name: repo_analyzer
description: "Repo 流水线：读取关键文件并定位问题。"
metadata:
  short-description: "read_file → diagnosis"
---

# repo_analyzer（app / Analyze）

## 目标

- 使用 `read_file` 读取 `app.py`、`test_app.py`
- 输出：问题定位 + 最小修复建议

## 必须使用的工具

- `read_file`

