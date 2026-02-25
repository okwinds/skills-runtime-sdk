---
name: ci_log_analyzer
description: "CI 排障（人类应用示例）：复现失败并提炼问题定位。"
metadata:
  short-description: "Analyze：shell_exec(pytest) 复现失败。"
---

# ci_log_analyzer（app / Analyze）

## 目标

- 通过 `shell_exec` 复现 CI 失败（本示例为 `pytest`）
- 提炼最小可修复点（哪一行/哪个函数导致失败）

