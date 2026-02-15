---
name: repo_patcher
description: "修复者：产出 apply_patch 补丁并落盘（workflow 示例：Fixer 角色）。"
metadata:
  short-description: "Fixer：最小补丁 + 可审计 approvals 证据。"
---

# repo_patcher（workflow / Fixer）

## 目标

根据 reviewer 建议，把修复以 **可审计** 的方式落到 workspace：
- 使用 `apply_patch` 修改文件
- 只做与修复相关的最小改动

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].repo_patcher`
- 任务文本包含要修复的文件与目标行为

## 必须使用的工具

- `apply_patch`

## 输出要求

- 先调用 `apply_patch` 成功落盘
- 然后输出一句话总结 + 建议 QA 命令

