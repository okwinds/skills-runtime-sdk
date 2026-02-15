---
name: path_a_worker
description: "分支 A 执行器：生成 A 分支产物并落盘。"
metadata:
  short-description: "Worker(A)：file_write outputs/path_a.md"
---

# path_a_worker（workflow / Branch Worker A）

## 目标

在 A 分支下生成可检查产物：
- `outputs/path_a.md`

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].path_a_worker`

## 必须使用的工具

- `file_write`：写入 `outputs/path_a.md`

## 输出要求

- 必须创建 `outputs/path_a.md`
- 内容应说明“这是 A 分支产物”

