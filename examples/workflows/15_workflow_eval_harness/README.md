# 15_workflow_eval_harness（同一 workflow 多次运行 → 对比 artifacts → 输出 score）

本目录提供一个“workflow eval harness”：用于把一个 workflow 当成**可评测对象**（而不是一次性脚本），对其进行：

- 同一 workflow 运行 N 次
- 收集关键 artifacts
- 规范化（normalize）后进行一致性对比
- 输出 score + diff 摘要（Markdown + JSON）

默认评测目标（可改 CLI 参数）：
- WF04：`04_map_reduce_parallel_subagents`
- WF05：`05_multi_agent_code_review_fix_qa_report`

## 运行方式（离线）

```bash
python3 examples/workflows/15_workflow_eval_harness/run.py --workspace-root /tmp/srsdk-eval
```

自定义次数与目标：

```bash
python3 examples/workflows/15_workflow_eval_harness/run.py --workspace-root /tmp/srsdk-eval --runs 3 --workflows 04,05
```

## 输出

在 `--workspace-root` 下生成：
- `eval_report.md`：人类可读报告（每个 artifact 一致性 + diff 摘要）
- `eval_score.json`：结构化结果（便于 CI/看板接入）
- `runs/`：每次运行的完整 workspace（默认保留，便于定位问题）

## 说明：为什么要 normalize

workflow 的 artifacts 往往包含：
- workspace 绝对路径
- WAL run_id（例如 `.skills_runtime_sdk/runs/<run_id>/events.jsonl`）

这些字段会导致“同一逻辑不同次运行”产生文本差异，eval harness 会先做规范化再比较，避免误报。

