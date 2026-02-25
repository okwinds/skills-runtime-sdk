---
name: rag_stub_runner
description: "最小 RAG：调用 kb_search（离线 stub）检索，再落盘检索结果与回答。"
metadata:
  short-description: "RAG stub：自定义检索 tool + 可审计产物（retrieval/report）。"
---

# rag_stub_runner（workflow / Minimal RAG Stub）

## 目标

在离线可回归的约束下，演示“检索 → 回答”的最小落地形态：

- 调用自定义工具 `kb_search` 获取命中结果
- 将命中落盘为 `retrieval.json`
- 输出一段基于命中的回答，并落盘到 `report.md`

## 必须使用的工具

- `kb_search`：离线 stub 检索（确定性）
- `file_write`：落盘产物（写操作，通常需要 approvals）

## 约束

- 默认离线：不依赖外网/真实 key
- 产物必须写在 workspace 内（相对路径优先）

