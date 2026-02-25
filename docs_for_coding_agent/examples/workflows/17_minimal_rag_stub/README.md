# 17_minimal_rag_stub（最小 RAG：离线 stub / Skills-First）

本示例演示一个最小的“检索 → 回答”形态，但**不依赖外部向量库/外网**：

- 用一个自定义 tool `kb_search` 做关键词检索（内置小语料，确定性）
- 通过 builtin tools `file_write` 落盘 `retrieval.json` 与 `report.md`（走 approvals，WAL 可审计）

关键约束：**skills-first** —— 任务文本包含 `$[examples:workflow].rag_stub_runner`，WAL 中必须出现 `skill_injected`。

## 如何运行（离线）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/workflows/17_minimal_rag_stub/run.py --workspace-root /tmp/srsdk-wf17
```

预期：

- stdout 含：`EXAMPLE_OK: workflows_17`
- workspace 下生成：
  - `retrieval.json`：检索命中结果（确定性）
  - `report.md`：汇总（包含 query、命中与答案）
- WAL（`events.jsonl`）中应出现：
  - `skill_injected`
  - `tool_call_finished`（tool 为 `kb_search` 与 `file_write`）
  - `approval_requested/approval_decided`（因为 safety.mode=ask）
