---
name: retry_controller
description: "重试控制器：定义重试预算与降级策略，并将策略写入 retry_plan.json。"
metadata:
  short-description: "Controller：update_plan + file_write(retry_plan.json)"
---

# retry_controller（workflow / Retry Controller）

## 目标

把“重试/降级策略”变成可审计产物：
- `update_plan`：记录重试预算与阶段
- `file_write`：写入 `retry_plan.json`

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].retry_controller`

## 必须使用的工具

- `update_plan`
- `file_write`

## 输出要求

- 必须写入 `retry_plan.json`
- plan 中必须包含：attempt 次数预算、降级策略与汇总步骤

