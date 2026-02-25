---
name: degrade_worker
description: "降级执行器：当重试耗尽后，生成最小可用 fallback 产物。"
metadata:
  short-description: "Degrade：file_write outputs/fallback.md"
---

# degrade_worker（workflow / Degrade Worker）

## 目标

在重试耗尽后生成 fallback 结果：
- 写入 `outputs/fallback.md`

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].degrade_worker`

## 必须使用的工具

- `file_write`

## 输出要求

- 必须创建 `outputs/fallback.md`
- 内容应说明：这是降级路径生成的最小可用结果

