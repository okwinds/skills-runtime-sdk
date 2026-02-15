---
name: master_planner
description: "总规划：把一个任务拆解为多个互不依赖子任务，并用 update_plan 同步进度（workflow 示例：Planner 角色）。"
metadata:
  short-description: "Planner：拆解任务 + 更新 plan + 落盘 subtasks.json（可审计）。"
---

# master_planner（workflow / Planner）

## 目标

把“一个大任务”拆成若干 **相互独立、互不影响** 的子任务，并形成可审计的计划与拆解产物：
- 用 `update_plan` 记录计划（Plan 是证据链的一部分）
- 用 `file_write` 落盘 `subtasks.json`（子任务定义）

## 输入约定

- 你的任务文本中会包含 mention：`$[examples:workflow].master_planner`。
- 你需要输出的子任务必须：
  - 相互独立（并行可执行）
  - 每个子任务有明确产物路径（例如 `outputs/<name>.md`）

## 必须使用的工具

- `update_plan`：更新结构化计划（生成 `plan_updated` 事件）
- `file_write`：写入 `subtasks.json`（写操作通常需要 approvals）

## `subtasks.json` 建议结构（示例）

```json
{
  "task_title": "...",
  "subtasks": [
    {"id": "research", "title": "...", "artifact_path": "outputs/research.md"},
    {"id": "design", "title": "...", "artifact_path": "outputs/design.md"}
  ]
}
```

## 输出要求

- 先 `update_plan`，再 `file_write subtasks.json`
- 简短总结：子任务数量 + 每个子任务的产物路径

