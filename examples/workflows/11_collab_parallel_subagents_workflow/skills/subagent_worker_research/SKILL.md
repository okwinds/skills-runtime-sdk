---
name: subagent_worker_research
description: "子 agent（Research）：生成澄清/边界产物并落盘。"
metadata:
  short-description: "Subagent(Research)：file_write outputs/research.md（可包含 inbox 输入）"
---

# subagent_worker_research（workflow / Subagent Research）

## 目标

生成独立产物：
- `outputs/research.md`

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].subagent_worker_research`
- 可能会通过 `send_input` 收到补充输入（可在产物中体现）

## 必须使用的工具

- `file_write`

## 输出要求

- 必须写入 `outputs/research.md`

