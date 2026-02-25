---
name: resume_reporter
description: "汇总：写 report.md 记录 src/dst run、fork 点与 evidence 指针（workflow 示例：Reporter 角色）。"
metadata:
  short-description: "Reporter：file_write(report.md)。"
---

# resume_reporter（workflow / Reporter）

## 目标

生成一份可审计报告，至少包含：
- src_run_id / dst_run_id
- fork index（0-based）
- 两次 run 的 wal_locator 指针
- checkpoint/final 产物路径

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].resume_reporter`

## 必须使用的工具

- `file_write`

