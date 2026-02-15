---
name: checkpoint_writer
description: "断点：写入 checkpoint 产物并确保可审计（workflow 示例：Checkpoint Writer 角色）。"
metadata:
  short-description: "Checkpoint：file_write(checkpoint.txt) 作为断点产物。"
---

# checkpoint_writer（workflow / Checkpoint Writer）

## 目标

在 workflow 的早期步骤落一个 checkpoint 产物，便于后续：
- 断点续做（fork/resume）
- 审计定位（WAL 证据）

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].checkpoint_writer`

## 必须使用的工具

- `file_write`：写入 `checkpoint.txt`（写操作通常需要 approvals）

## 输出要求

- checkpoint 内容应可迁移（不要写死业务）

