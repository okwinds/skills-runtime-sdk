---
name: ci_patcher
description: "CI 修复（人类应用示例）：使用 apply_patch 做最小修复。"
metadata:
  short-description: "Patch：apply_patch 最小补丁。"
---

# ci_patcher（app / Patch）

## 目标

对目标文件做最小修复：
- 不做无关格式化
- 优先修复测试失败的根因

## 必须使用的工具

- `apply_patch`

