---
name: form_validator
description: "对采集到的结构化字段做最小确定性校验，并给出回归口径（workflow 示例：Validate 角色）。"
metadata:
  short-description: "Validate：建议用 shell_exec 做确定性断言（避免依赖外网）。"
---

# form_validator（workflow / Validate）

## 目标

对访谈采集结果进行最小且可复现的校验：
- 字段齐全
- 基本格式正确（例如 email 包含 `@`）
- 结果可落盘并可被后续脚本读取

## 推荐工具组合

- `update_plan`：记录阶段性进度（审计友好）
- `shell_exec`：执行最小确定性断言（例如 `python -c` 解析 JSON）

