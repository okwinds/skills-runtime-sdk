---
name: repo_patcher
description: "在 workspace 内生成确定性的补丁并通过 apply_patch 落盘（workflow 示例：Patch 角色）。"
metadata:
  short-description: "Patch：产出 apply_patch 补丁并落盘，必须可审计、可回归。"
---

# repo_patcher（workflow / Patch）

## 目标

根据分析结论，把修复以 **可审计** 的形式落到 workspace 中：
- 使用 `apply_patch`（Codex 风格 patch 文本）修改文件
- 尽量最小改动，避免无关格式化

## 输入约定

- 你的任务文本中会包含 mention：`$[examples:workflow].repo_patcher`。
- 你应已知道要修复哪个文件/哪段逻辑（例如 `app.py` 的一个函数）。

## 必须使用的工具

- `apply_patch`：对 workspace 文件应用补丁（写操作，通常需要 approvals）

## Patch 约束

- 所有路径必须在 workspace 内（相对路径优先）
- 采用最小 context 的 `*** Update File:` hunk，保证可匹配
- 不要引入与修复无关的改动（例如全文件 reformat）

## 输出要求

先调用 `apply_patch`；tool result 成功后再用 1-2 句说明：
- 修复了什么
- 建议的 QA 命令/断言

## 失败处理

若 `apply_patch` 返回 `validation/not_found/permission`：
- 说明错误类别
- 给出下一步：补充 context、确认文件路径、或请求 approvals

