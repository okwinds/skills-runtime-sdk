---
name: workflow_policy
description: "提供可复用的修复政策/规则（通过 references/ 读取），用于指导 patch 与 QA（workflow 示例：Policy 角色）。"
metadata:
  short-description: "Policy：skill_ref_read 读取 references/policy.md，指导后续步骤。"
---

# workflow_policy（workflow / Policy）

## 目标

通过 `references/` 提供可复用的“政策/规则/约束”，供 agent 在运行期读取并遵循。

## 必须使用的工具

- `skill_ref_read`：读取 `references/policy.md`（需要在配置中显式开启 `skills.references.enabled`）

## 约束

- 引用文件必须在 bundle 内，且路径必须位于 `references/` 下（默认不允许读取 assets/）

