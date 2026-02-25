---
name: runbook_writer
description: "排障助手（人类应用示例）：生成可执行的 runbook，并写入 workspace。"
metadata:
  short-description: "Runbook：file_write(runbook.md)。"
---

# runbook_writer（app / Runbook）

## 目标

输出一个可执行的 runbook：
- 可能原因（假设列表）
- 排查步骤（按优先级排序）
- 风险与回滚点（如适用）

## 必须使用的工具

- `file_write`（写入 `runbook.md`）

