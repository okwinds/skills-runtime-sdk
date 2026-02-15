---
name: master_collab_planner
description: "主控编排：用 spawn_agent/send_input/wait 管理并行子 agent（Collab 原语）。"
metadata:
  short-description: "Master：spawn_agent + send_input + wait（并行子 agent 生命周期）"
---

# master_collab_planner（workflow / Collab Master）

## 目标

用 Collab 原语完成并行子 agent 的生命周期管理：
- `spawn_agent`：生成多个子 agent
- `send_input`：给子 agent 追加输入（inbox）
- `wait`：等待子 agent 完成并拿到 final_output

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].master_collab_planner`
- 子 agent 的任务文本必须包含各自的 skill mention（skills-first）

## 必须使用的工具

- `spawn_agent`
- `send_input`
- `wait`

## 输出要求

- 至少 spawn 3 个子 agent
- 对每个子 agent 至少 send_input 1 次
- wait 等待所有子 agent 完成

