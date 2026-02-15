---
name: path_b_worker
description: "分支 B 执行器：生成 B 分支产物并落盘。"
metadata:
  short-description: "Worker(B)：file_write outputs/path_b.md"
---

# path_b_worker（workflow / Branch Worker B）

## 目标

在 B 分支下生成可检查产物：
- `outputs/path_b.md`

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].path_b_worker`

## 必须使用的工具

- `file_write`：写入 `outputs/path_b.md`

## 输出要求

- 必须创建 `outputs/path_b.md`
- 内容应说明“这是 B 分支产物”

