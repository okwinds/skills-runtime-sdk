# state（WAL / resume replay / fork）

本目录的示例用于演示：
- `.skills_runtime_sdk/runs/<run_id>/events.jsonl` 的落盘位置
- `resume_strategy=replay` 如何从 WAL 重建 history
- `fork_run(...)` 如何从某个事件点分叉出新的 run

