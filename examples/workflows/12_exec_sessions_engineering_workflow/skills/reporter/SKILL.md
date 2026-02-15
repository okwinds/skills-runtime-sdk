---
name: reporter
description: "汇总报告：把 exec sessions 的关键标记与证据路径写入 report.md。"
metadata:
  short-description: "Reporter：file_write report.md（包含 markers + events_path）"
---

# reporter（workflow / Reporter）

## 目标

沉淀报告：
- 写入 `report.md`
- 包含关键标记（READY / ECHO:hello / BYE）是否出现
- 包含会话 run 的 `events_path` 指针

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].reporter`

## 必须使用的工具

- `file_write`

## 输出要求

- 必须写入 `report.md`
- 报告必须能指向证据（events_path）

