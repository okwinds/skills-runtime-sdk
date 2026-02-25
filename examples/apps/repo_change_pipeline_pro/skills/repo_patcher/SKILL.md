---
name: repo_patcher
description: "Repo 流水线：应用最小补丁修复。"
metadata:
  short-description: "apply_patch minimal"
---

# repo_patcher（app / Patch）

## 目标

- 对目标文件做最小修改修复问题
- 禁止大范围重构（示例强调可回归与可审计）

## 必须使用的工具

- `apply_patch`

