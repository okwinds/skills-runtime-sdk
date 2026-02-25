---
name: repo_patcher
description: "在 workspace 内生成确定性的补丁并通过 apply_patch 落盘（workflow 示例：Patch 角色）。"
metadata:
  short-description: "Patch：产出 apply_patch 补丁并落盘，必须遵守 policy 约束。"
---

# repo_patcher（workflow / Patch）

## 目标

在遵守 policy 的前提下，将修复以 `apply_patch` 落盘。

## 必须使用的工具

- `apply_patch`

