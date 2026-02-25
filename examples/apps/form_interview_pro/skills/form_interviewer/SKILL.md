---
name: form_interviewer
description: "表单访谈（人类应用示例）：使用 request_user_input 结构化收集字段，并在必要时追问。"
metadata:
  short-description: "Interview：request_user_input 收集字段。"
---

# form_interviewer（app / Interview）

## 目标

在一次 run 中完成结构化访谈：
- 明确要收集的字段与问题 id
- 使用 `request_user_input` 获取 answers
- 对不明确/不合法输入进行追问（有限次数）

## 必须使用的工具

- `request_user_input`

