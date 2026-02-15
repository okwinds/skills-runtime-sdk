---
name: reporter
description: "汇总报告：把本次 workflow 的关键证据与产物路径写入 report.md。"
metadata:
  short-description: "Reporter：file_write report.md（包含 events_path 指针）"
---

# reporter（workflow / Reporter）

## 目标

把一次 workflow 的结果沉淀为可复核报告：
- `report.md`
- 应包含每个步骤的 `events_path` 指针与产物路径

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].reporter`

## 必须使用的工具

- `file_write`：写入 `report.md`

## 输出要求

- 必须写入 `report.md`
- 报告必须包含：route 结果、产物路径、以及对应 run 的 `events_path`

