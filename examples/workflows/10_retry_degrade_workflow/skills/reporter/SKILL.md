---
name: reporter
description: "汇总报告：把重试/失败/降级的证据链写入 report.md。"
metadata:
  short-description: "Reporter：file_write report.md（包含 exit_code + wal_locator）"
---

# reporter（workflow / Reporter）

## 目标

把一次“重试→降级”的执行过程沉淀为报告：
- `report.md` 必须包含：
  - 每次 attempt 的 `wal_locator`
  - attempt 的 exit_code / ok
  - 降级产物路径

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].reporter`

## 必须使用的工具

- `file_write`

## 输出要求

- 必须写入 `report.md`
- 报告内容必须可复核（能根据 wal_locator 找到证据）

