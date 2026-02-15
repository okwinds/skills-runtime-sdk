---
name: repo_reporter
description: "报告生成：写 report.md 汇总 skill_exec 产物与 evidence 指针。"
metadata:
  short-description: "Reporter：file_write(report.md)。"
---

# repo_reporter（workflow / Reporter）

## 目标

生成一份可审计报告，至少包含：
- `skill_exec` 的 events_path
- `action_artifact.json` 的摘要

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].repo_reporter`

## 必须使用的工具

- `file_write`

