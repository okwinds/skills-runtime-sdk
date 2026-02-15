---
name: router
description: "路由器：读取输入并做分支决策，把决策写入 route.json（可审计）。"
metadata:
  short-description: "Router：read_file → file_write(route.json)"
---

# router（workflow / Branch Router）

## 目标

把“分支选择”变成可审计的决策：
- 读取 `task_input.json`
- 输出 `route.json`（例如 `{ \"route\": \"A\" }`）

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].router`
- `task_input.json` 在 workspace 根目录

## 必须使用的工具

- `read_file`：读取 `task_input.json`
- `file_write`：写入 `route.json`

## 输出要求

- 必须写入 `route.json`
- 简短说明选择了哪个分支（A/B）以及依据（从 input 中得出即可）

