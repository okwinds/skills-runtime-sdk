---
name: repo_reporter
description: "报告生成：汇总 Review/Fix/QA 的结果并写 report.md（workflow 示例：Reporter 角色）。"
metadata:
  short-description: "Reporter：file_write(report.md)，包含 wal_locator 指针。"
---

# repo_reporter（workflow / Reporter）

## 目标

生成一份可审计报告：
- 列出每个步骤的摘要
- 列出每个步骤对应的 `wal_locator`

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].repo_reporter`
- 任务文本会包含各步骤的摘要与 wal_locator（或你需要读取）

## 必须使用的工具

- `file_write`

## 输出要求

- 写入 `report.md`

