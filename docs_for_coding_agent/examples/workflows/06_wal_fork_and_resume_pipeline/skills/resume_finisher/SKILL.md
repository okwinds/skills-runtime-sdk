---
name: resume_finisher
description: "续做：基于 replay resume 继续执行剩余步骤并写最终产物（workflow 示例：Resume Finisher 角色）。"
metadata:
  short-description: "Resume：read_file(checkpoint) → file_write(final.txt)。"
---

# resume_finisher（workflow / Resume Finisher）

## 目标

在 fork 后的新 run 中继续完成剩余步骤：
- 读取 checkpoint（确认断点产物存在）
- 写入最终产物（例如 `final.txt`）

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].resume_finisher`
- 运行配置通常会开启 `run.resume_strategy=replay`（尽量恢复 tool outputs 与 approvals cache）

## 必须使用的工具

- `read_file`
- `file_write`

