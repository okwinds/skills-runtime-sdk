# 12_exec_sessions_engineering_workflow（exec_command/write_stdin 工程式交互 / Skills-First）

本示例演示一个“工程流”形态：启动一个交互式会话（PTY）→ 写入输入 → 解析输出 → 生成报告。

关键点：
- 使用 builtin tools：`exec_command` + `write_stdin`
- **在 Agent loop 内直接调用 exec sessions 工具**（不是 CLI 工具模式）
- Skills-First：能力必须由 `skills/*/SKILL.md` 定义
- 默认离线可回归（无需外网、无需真实 key）

## 运行方式（离线）

```bash
python3 docs_for_coding_agent/examples/workflows/12_exec_sessions_engineering_workflow/run.py --workspace-root /tmp/srsdk-demo
```

## 你应该看到什么

- `report.md`：报告会包含对关键输出标记的解析结果（READY / ECHO:hello / BYE）
- `.skills_runtime_sdk/runs/<run_id>/events.jsonl`：WAL 证据链（可审计）

## 对应 skills

- `skills/session_operator/SKILL.md`
- `skills/reporter/SKILL.md`
