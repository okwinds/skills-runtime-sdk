---
name: form_interviewer
description: "多轮采集结构化字段（表单访谈）：提问→收集→澄清（workflow 示例：Interview 角色）。"
metadata:
  short-description: "Interview：用 request_user_input 结构化采集字段。"
---

# form_interviewer（workflow / Interview）

## 目标

在一个 workspace 的一次 run 中，以“多轮/多题”方式收集用户信息，输出结构化 answers，供后续验证与落盘。

## 必须使用的工具

- `request_user_input`：结构化向用户提问并获取 answers。

## 设计要点

- 问题 id 必须稳定（便于代码侧映射与审计）；
- 如果提供 options，则 labels 必须唯一；
- 离线回归可以用 scripted HumanIOProvider 注入答案（避免阻塞）。

