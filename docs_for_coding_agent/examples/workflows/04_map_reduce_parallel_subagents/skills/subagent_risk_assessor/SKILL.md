---
name: subagent_risk_assessor
description: "子任务执行者：产出风险/验收口径类产物（workflow 示例：并行子 agent 之一）。"
metadata:
  short-description: "Subagent：输出独立产物（只写自己的文件，不碰别人）。"
---

# subagent_risk_assessor（workflow / Subagent）

## 目标

根据子任务描述，生成一份“风险与验收口径”类产物（Markdown），并写入约定路径（例如 `outputs/risks.md`）。

## 输入约定

- 任务文本中包含 mention：`$[examples:workflow].subagent_risk_assessor`
- 任务文本会告诉你产物路径（你只能写这个文件）

## 必须使用的工具

- `file_write`

## 约束

- 只写入你被分配的产物路径
- 不要修改其它文件

