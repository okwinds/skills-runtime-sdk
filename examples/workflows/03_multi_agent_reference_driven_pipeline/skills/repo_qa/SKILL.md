---
name: repo_qa
description: "在 workspace 内执行确定性的回归验证，给出可复现证据（workflow 示例：QA 角色）。"
metadata:
  short-description: "QA：用 shell_exec 跑确定性断言，并输出稳定关键字。"
---

# repo_qa（workflow / QA）

## 目标

用最小确定性命令对 patch 结果做回归验证，输出证据（stdout）。

## 必须使用的工具

- `shell_exec`

