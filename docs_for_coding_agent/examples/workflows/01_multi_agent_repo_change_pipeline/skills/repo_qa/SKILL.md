---
name: repo_qa
description: "在 workspace 内执行确定性的回归验证，给出可复现证据（workflow 示例：QA 角色）。"
metadata:
  short-description: "QA：用 shell_exec 跑确定性断言，输出 PASS/FAIL 证据。"
---

# repo_qa（workflow / QA）

## 目标

对 patch 的结果做最小、确定性的回归验证：
- 运行可复现的检查命令
- 产出可作为证据的 stdout/stderr（在 WAL 中可检索）

## 输入约定

- 你的任务文本中会包含 mention：`$[examples:workflow].repo_qa`。
- workspace 已包含修复后的文件（例如 `app.py`）。

## 必须使用的工具

- `shell_exec`：执行验证命令（执行操作，通常需要 approvals）

## 建议做法

- 使用 `python -c` + `assert` 做最小验证（避免依赖额外框架）
- 成功时打印稳定关键字（例如 `QA_OK`），便于 smoke tests 与日志检索

## 输出要求

tool 执行完成后，用 1 句总结：
- 验证通过/失败
- 若失败，指出最可能原因与下一步

