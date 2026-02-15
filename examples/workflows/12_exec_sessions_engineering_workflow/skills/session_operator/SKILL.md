---
name: session_operator
description: "会话操作者：在 agent loop 内使用 exec_command/write_stdin 完成交互式工程流。"
metadata:
  short-description: "ExecSessions：exec_command + write_stdin（PTY 交互）"
---

# session_operator（workflow / Exec Sessions Operator）

## 目标

完成一次可审计的交互式会话：
- `exec_command` 启动会话（PTY-backed）
- `write_stdin` 写入输入并读取输出

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].session_operator`

## 必须使用的工具

- `exec_command`
- `write_stdin`

## 输出要求

- 必须调用 `exec_command` 启动会话
- 必须至少调用 `write_stdin` 两次（体现“交互式”）
- 关键输出应包含 READY/ECHO/BYE（本示例使用固定脚本）

