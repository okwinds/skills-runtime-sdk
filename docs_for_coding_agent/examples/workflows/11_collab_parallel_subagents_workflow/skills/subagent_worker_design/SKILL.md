---
name: subagent_worker_design
description: "子 agent（Design）：生成方案草图/接口草案产物并落盘。"
metadata:
  short-description: "Subagent(Design)：file_write outputs/design.md（可包含 inbox 输入）"
---

# subagent_worker_design（workflow / Subagent Design）

## 目标

生成独立产物：
- `outputs/design.md`

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].subagent_worker_design`
- 可能会通过 `send_input` 收到补充输入

## 必须使用的工具

- `file_write`

## 输出要求

- 必须写入 `outputs/design.md`

