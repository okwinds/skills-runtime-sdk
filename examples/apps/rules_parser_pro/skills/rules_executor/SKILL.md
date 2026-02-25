---
name: rules_executor
description: "规则执行（人类应用示例）：根据 plan 产出 result.json，并用 shell_exec 做最小 QA。"
metadata:
  short-description: "Execute：result.json + 最小 QA。"
---

# rules_executor（app / Execute）

## 目标

- 生成 `result.json`
- 使用 `shell_exec` 做最小确定性 QA（结构断言）

## 推荐工具

- `shell_exec`

