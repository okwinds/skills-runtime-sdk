---
name: content_redactor
description: "合规示例：对目标文件做最小脱敏补丁（apply_patch）。"
metadata:
  short-description: "patch minimal"
---

# content_redactor（app / Redact）

## 目标

- 基于 policy 规则，对 `target.md` 做**最小替换**
- 输出补丁应避免重写无关段落（降低误伤）

## 必须使用的工具

- `apply_patch`

