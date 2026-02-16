---
name: rules_parser
description: "将自然语言规则转成结构化 plan，并以确定性方式执行（workflow 示例：规则解析器）。"
metadata:
  short-description: "规则解析：plan + 确定性执行 + 产物落盘 + 可审计证据链。"
---

# rules_parser（workflow / Rules Parser）

## 目标

把业务给出的“自然语言规则”转成可执行的结构化 plan，并在可审计的证据链下落盘产物：

- `plan.json`：结构化、可执行的提取计划
- `result.json`：按 plan 的确定性执行结果
- `report.md`：汇总（包含关键输入、产物指针与 events_path）

## 输入约定

- 你的任务文本中会包含 mention：`$[examples:workflow].rules_parser`。
- 输入数据可以是一个 code_string（例如包含数字与下划线的编码）。

## 必须使用的工具

- `file_write`：将 `plan.json` / `result.json` / `report.md` 落盘到 workspace（写操作，通常需要 approvals）

## 约束

- plan 必须可由确定性逻辑执行（不要输出需要外部依赖/不可复现的步骤）。
- 所有路径必须在 workspace 内（相对路径优先）。

## 输出要求

1) 先生成 plan（结构稳定、可审计）
2) 再执行 plan 得到 result（确定性）
3) 最后输出 report（包含 evidence 指针）

