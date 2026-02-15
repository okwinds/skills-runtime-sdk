---
name: result_aggregator
description: "总汇总：读取各子任务产物并生成最终报告（workflow 示例：Aggregator 角色）。"
metadata:
  short-description: "Aggregator：read_file 汇总 inputs → file_write 输出 report.md。"
---

# result_aggregator（workflow / Aggregator）

## 目标

把多个子任务的产物与执行摘要汇总为一个最终报告：
- 读取 `outputs/*.md`（或任务文本指定的路径）
- 生成 `report.md`，并包含每个子任务的 `events_path` 指针（便于审计）

## 输入约定

- 任务文本中会包含 mention：`$[examples:workflow].result_aggregator`
- 任务文本会提供：
  - 子任务产物路径列表
  - 每个子任务的 events_path（或你需要自己读）

## 必须使用的工具

- `read_file`：读取子任务产物（只读）
- `file_write`：写入 `report.md`（写操作通常需要 approvals）

## 输出要求

- 报告必须列出每个子任务：
  - 产物路径
  - events_path
  - 简短摘要

