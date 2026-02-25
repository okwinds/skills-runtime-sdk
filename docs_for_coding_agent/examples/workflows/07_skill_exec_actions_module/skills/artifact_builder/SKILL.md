---
name: artifact_builder
description: "动作型 Skill：通过 skill_exec 执行 actions/ 脚本，生成可审计产物。"
metadata:
  short-description: "Actions：声明 build action，并由 skill_exec 调用。"
actions:
  build:
    kind: shell
    argv: ["python3", "actions/build_artifact.py"]
    timeout_ms: 5000
    env:
      ARTIFACT_KIND: "demo"
---

# artifact_builder（workflow / Skill Actions）

## 目标

提供一个可复用的“动作型 Skill”示例：
- 将可执行脚本放入 bundle 的 `actions/` 目录
- 在 `SKILL.md` frontmatter 的 `actions` 声明 action（argv/timeout/env）
- 运行期通过 `skill_exec` 执行（保持 approvals/sandbox/WAL 证据链）

## 输入约定

- 任务文本包含 mention：`$[examples:workflow].artifact_builder`
- 运行期需要启用：`skills.actions.enabled=true`（默认禁用）

## 必须使用的工具

- `skill_exec`：执行 frontmatter `actions` 中声明的 action

## 输出要求

执行 action 后，应在 workspace 看到产物文件（本示例为 `action_artifact.json`）。
