# 01_multi_agent_repo_change_pipeline（多 Agent 项目流水线：Skills-First）

本示例演示一个“能做项目”的最小流水线形态：

1. **Analyze**：读取目标文件，定位 bug 与修复策略  
2. **Patch**：以 `apply_patch` 形式落补丁（写操作，走 approvals）  
3. **QA**：以 `shell_exec` 执行确定性验证（执行操作，走 approvals）  
4. **Report**：写出 `report.md`（写操作，走 approvals）

关键约束：**所有 agent 能力都必须基于 Skills（`SKILL.md`）最小单元构建**。  
本示例中，每个角色对应一个 skill，并通过 mention（`$[account:domain].skill_name`）触发注入与 `skill_injected` 证据事件。

## 目录结构

```
docs_for_coding_agent/examples/workflows/01_multi_agent_repo_change_pipeline/
  run.py
  skills/
    repo_analyzer/SKILL.md
    repo_patcher/SKILL.md
    repo_qa/SKILL.md
    repo_reporter/SKILL.md
```

## 如何运行（离线）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/workflows/01_multi_agent_repo_change_pipeline/run.py --workspace-root /tmp/srsdk-demo
```

预期：
- 运行成功退出（exit=0）
- 输出包含：`EXAMPLE_OK: workflows_01`
- `report.md` 生成在 `--workspace-root` 指定目录下
- 每个子 agent 的 WAL（`events.jsonl`）中包含至少 1 条 `skill_injected`

## 你应该观察什么（证据链）

1. **skills 注入**
   - 事件：`skill_injected`
   - payload：包含 `mention_text`（例如 `$[examples:workflow].repo_patcher`）

2. **approvals**
   - 事件：`approval_requested` / `approval_decided`
   - 本示例用 scripted provider 自动批准，保证离线可回归

3. **tools 执行**
   - 事件：`tool_call_requested` / `tool_call_started` / `tool_call_finished`
   - `apply_patch` 的 result.data.changes 应包含 update 变更
   - `shell_exec` 的 stdout 应包含 `QA_OK`
