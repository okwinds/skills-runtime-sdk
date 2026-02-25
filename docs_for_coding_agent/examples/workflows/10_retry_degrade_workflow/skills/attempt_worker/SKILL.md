---
name: attempt_worker
description: "Attempt 执行器：执行一次尝试（可能失败），失败也必须留下可审计证据。"
metadata:
  short-description: "Attempt：shell_exec（允许失败，报告 exit_code）"
---

# attempt_worker（workflow / Attempt Worker）

## 目标

执行一次 attempt，并确保失败也可审计：
- 使用 `shell_exec` 执行一个命令
- 即使 exit_code != 0，也需要在后续报告中体现（由 reporter 汇总）

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].attempt_worker`

## 必须使用的工具

- `shell_exec`

## 输出要求

- 必须执行一次 `shell_exec`
- 简短说明“本次 attempt 是否成功/失败”（失败在本示例是预期）

