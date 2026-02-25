---
name: data_fixer
description: "数据修复（人类应用示例）：按确定性规则生成 fixed.csv 与 validation_report.json。"
metadata:
  short-description: "Fix：file_write(fixed.csv/validation_report.json)。"
---

# data_fixer（app / Fix）

## 目标

按确定性规则修复：
1) 丢弃 email 为空的行  
2) quantity 非法或 <1 则设为 1  
3) 其余字段保持不变  

## 必须使用的工具

- `file_write`

