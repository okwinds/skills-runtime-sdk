---
name: data_import_fixer
description: "数据导入校验与修复：读取 CSV → 生成修复后的 CSV + 校验报告 → 运行离线 QA 校验。"
metadata:
  short-description: "Data Import Fixer：read_file(input.csv) → file_write(fixed.csv/report) → shell_exec(QA)"
---

# data_import_fixer（workflow / Data Import Fixer）

## 目标

对导入数据（CSV）执行“校验 + 自动修复”，并输出可复核的产物与证据指针：

- `fixed.csv`：修复后的可导入数据（规则确定性、可回归）
- `validation_report.json`：问题列表与修复动作（结构稳定）
- `report.md`：汇总（包含产物指针与 WAL 路径）

## 输入约定

- 你的任务文本会包含 mention：`$[examples:workflow].data_import_fixer`
- 输入文件默认在 workspace 内：`input.csv`

## 必须使用的工具

- `read_file`：读取 `input.csv`
- `file_write`：写入 `fixed.csv`、`validation_report.json`、`report.md`
- `shell_exec`：运行离线 QA 校验（stdout 需包含 `QA_OK`）

## 约束

- **离线可回归**：不得依赖外网、不得依赖真实密钥
- **确定性**：同一输入必须得到同一输出（含修复规则与报告结构）
- **路径限制**：所有读写必须在 workspace 内（相对路径优先）
