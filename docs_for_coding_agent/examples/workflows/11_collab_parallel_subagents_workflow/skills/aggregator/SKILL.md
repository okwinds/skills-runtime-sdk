---
name: aggregator
description: "汇总器：读取 outputs/* 并生成 report.md（包含证据路径指针）。"
metadata:
  short-description: "Aggregator：read_file(outputs/*) + file_write(report.md)"
---

# aggregator（workflow / Aggregator）

## 目标

把多个子任务产物汇总成可复核报告：
- 读取 `outputs/*`
- 生成 `report.md`（应包含每个子任务的产物路径与 wal_locator 指针）

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].aggregator`

## 必须使用的工具

- `read_file`
- `file_write`

## 输出要求

- 必须写入 `report.md`
- 报告必须能指向证据（wal_locator）与产物路径

