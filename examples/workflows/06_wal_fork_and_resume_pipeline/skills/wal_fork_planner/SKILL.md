---
name: wal_fork_planner
description: "Fork 规划：读取 events.jsonl 并选择 fork 点（workflow 示例：Fork Planner 角色）。"
metadata:
  short-description: "Fork Planner：read_file(WAL) → 输出 fork index 建议。"
---

# wal_fork_planner（workflow / Fork Planner）

## 目标

当一次 run 异常中断时，帮助选择一个“可恢复”的 fork 点：
- 读取 `.skills_runtime_sdk/runs/<run_id>/events.jsonl`
- 定位最近一次成功的关键步骤（例如 tool_call_finished ok=true）
- 输出建议的 `up_to_index_inclusive`（0-based 行号）

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].wal_fork_planner`
- 任务文本会给出 src_run_id 与你要找的关键事件

## 必须使用的工具

- `read_file`：读取 WAL（只读）

## 输出要求

- 输出一个明确的 fork 行号与理由（例如：最后一次 checkpoint 写入成功）

