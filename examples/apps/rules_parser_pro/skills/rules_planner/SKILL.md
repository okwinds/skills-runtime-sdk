---
name: rules_planner
description: "规则解析（人类应用示例）：把自然语言规则转成结构化 plan.json。"
metadata:
  short-description: "Plan：自然语言规则→plan.json。"
---

# rules_planner（app / Plan）

## 目标

将用户提供的规则文本转成一个可审计的结构化 plan（JSON）：
- 清晰声明输入字段（例如 `code_string`）
- steps 使用白名单 op（len/slice/index/contains/...）

## 必须使用的工具

- `file_write`（写入 `plan.json`）

