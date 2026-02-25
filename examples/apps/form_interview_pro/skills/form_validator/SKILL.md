---
name: form_validator
description: "表单校验（人类应用示例）：做最小确定性校验，并在失败时触发追问。"
metadata:
  short-description: "Validate：最小规则校验（email/quantity）。"
---

# form_validator（app / Validate）

## 目标

对已收集字段做最小校验：
- `email`：包含 `@`
- `quantity`：正整数

如果不满足，应回到访谈步骤追问（最多 1 次），避免死循环。

## 推荐工具（可选）

- `shell_exec`：对 `submission.json` 做确定性校验（便于回归与审计）。

