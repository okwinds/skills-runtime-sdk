---
name: subagent_researcher
description: "子任务执行者：产出研究/澄清类产物（workflow 示例：并行子 agent 之一）。"
metadata:
  short-description: "Subagent：输出独立产物（只写自己的文件，不碰别人）。"
---

# subagent_researcher（workflow / Subagent）

## 目标

根据子任务描述，生成一份“澄清/研究”类的可迁移产物（Markdown），并写入约定路径（例如 `outputs/research.md`）。

## 输入约定

- 任务文本中包含 mention：`$[examples:workflow].subagent_researcher`
- 任务文本会告诉你：
  - 子任务标题/目标
  - 产物路径（你只能写这个文件）

## 必须使用的工具

- `file_write`：写产物文件（写操作通常需要 approvals）

## 约束

- 只写入你被分配的产物路径
- 不要修改其它文件（保持子任务互不影响）

