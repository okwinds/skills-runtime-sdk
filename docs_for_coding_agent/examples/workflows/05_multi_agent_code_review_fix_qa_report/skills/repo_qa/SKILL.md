---
name: repo_qa
description: "质量验证：运行最小确定性断言（workflow 示例：QA 角色）。"
metadata:
  short-description: "QA：shell_exec 断言通过并输出稳定标记。"
---

# repo_qa（workflow / QA）

## 目标

对修复后的代码做最小确定性回归验证：
- 使用 `shell_exec` 执行断言脚本
- 必须输出一个稳定标记（例如 QA_OK）

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].repo_qa`
- 任务文本会给出建议的断言/命令（或你自己构造）

## 必须使用的工具

- `shell_exec`

## 输出要求

- tool stdout 中必须包含 `QA_OK`

