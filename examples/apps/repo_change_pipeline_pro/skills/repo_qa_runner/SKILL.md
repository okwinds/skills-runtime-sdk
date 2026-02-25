---
name: repo_qa_runner
description: "Repo 流水线：运行最小回归（pytest）。"
metadata:
  short-description: "shell_exec pytest"
---

# repo_qa_runner（app / QA）

## 目标

- 使用 `shell_exec` 运行 `python -m pytest -q`
- 输出：通过/失败摘要

## 必须使用的工具

- `shell_exec`

