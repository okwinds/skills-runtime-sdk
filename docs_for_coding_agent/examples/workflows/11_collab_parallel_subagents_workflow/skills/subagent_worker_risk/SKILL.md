---
name: subagent_worker_risk
description: "子 agent（Risk）：生成风险与验收口径产物并落盘。"
metadata:
  short-description: "Subagent(Risk)：file_write outputs/risks.md（可包含 inbox 输入）"
---

# subagent_worker_risk（workflow / Subagent Risk）

## 目标

生成独立产物：
- `outputs/risks.md`

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].subagent_worker_risk`
- 可能会通过 `send_input` 收到补充输入

## 必须使用的工具

- `file_write`

## 输出要求

- 必须写入 `outputs/risks.md`

