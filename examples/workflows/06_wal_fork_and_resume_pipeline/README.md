# 06_wal_fork_and_resume_pipeline（断点续做：WAL fork → replay resume / Skills-First）

本示例演示一个“断点续做”的项目级形态：

1. **第一次 run（Checkpoint Writer）**：写入一个 checkpoint 产物后，模拟进程异常/中断（run_failed）。
2. **Fork Planner**：读取 WAL（`events.jsonl`）并选择一个“安全的 fork 点”（示例：最后一次成功写 checkpoint 的事件行号）。
3. **Fork**：调用 `skills_runtime.state.fork.fork_run(...)` 生成新的 run（新 run_id 的 events.jsonl 前缀）。
4. **第二次 run（Resume Finisher）**：以 `run.resume_strategy=replay` 恢复历史与 approvals cache，继续完成剩余步骤，并写最终产物。
5. **Reporter**：写 `report.md` 汇总 src/dst run 与 evidence 指针。

注意：
- 本示例默认离线可回归（Fake backend），但展示的是 **真实的 WAL fork + replay resume 机制**（不是“打印概念”）。
- fork/resume 是“编排层”能力；**角色能力仍是 skill-first**。

## 如何运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/06_wal_fork_and_resume_pipeline/run.py --workspace-root /tmp/srsdk-wf06
```

产物：
- `checkpoint.txt`（第一次 run 写入）
- `final.txt`（第二次 run 写入）
- `report.md`

