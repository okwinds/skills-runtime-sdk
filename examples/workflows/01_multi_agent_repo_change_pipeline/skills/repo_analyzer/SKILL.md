---
name: repo_analyzer
description: "分析 workspace 内目标文件，定位问题并给出可执行修复策略（workflow 示例：Analyze 角色）。"
metadata:
  short-description: "Analyze：读文件→定位问题→输出修复建议与下一步。"
---

# repo_analyzer（workflow / Analyze）

## 目标

在一个 workspace（工作区）内，对“待修复的目标文件”进行最小必要的阅读与分析，输出：
- 问题定位（bug/缺陷的现象与原因）
- 修复策略（要改哪些点、风险与回归口径）
- 下一步计划（交给 patcher/qa/report）

## 输入约定

- 你的任务文本中会包含一个 skill mention：`$[examples:workflow].repo_analyzer`（用于注入与审计证据）。
- workspace 内通常会有一个最小 demo 项目文件（例如 `app.py`），你需要从中定位问题。

## 允许使用的工具（建议）

- `read_file`：读取目标文件内容（只读，不需要 approvals）。
- `grep_files`：在 workspace 内搜索关键字（只读，不需要 approvals）。

## 输出要求（建议格式）

请用以下结构输出（纯文本即可）：
1. 问题摘要（1-2 句）
2. 根因分析（必要时引用关键代码行）
3. 修复方案（具体到“要把哪一行改成什么语义”）
4. QA 回归口径（给出可执行的断言/命令）

## 失败处理

如果读取文件失败（路径不在 workspace 内/文件不存在），请：
- 明确说明失败原因
- 给出下一步可执行动作（例如先 `list_dir` 或让用户确认文件名）

