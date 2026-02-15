# 03_multi_agent_reference_driven_pipeline（多 Agent：References 驱动 / Skills-First）

本示例强调“Skill bundle 的 references”在项目落地中的作用：把可复用规则/政策/提示材料放进 skill 的 `references/`，由 agent 在运行期通过 `skill_ref_read` 读取并纳入决策与输出。

流程（多 agent 协同）：
1. **Policy**：读取 `references/policy.md`（`skill_ref_read`，不需要 approvals，但需要显式开启 `skills.references.enabled`）
2. **Patch**：根据 policy 约束生成并落 `apply_patch`（写操作，走 approvals）
3. **QA**：用 `shell_exec` 做确定性校验（走 approvals）
4. **Report**：写 `report.md`（走 approvals）

关键约束：**skills-first** —— 每个角色能力来自 `skills/*/SKILL.md`，任务文本包含对应 mention，WAL 中必须出现 `skill_injected`。

## 如何运行（离线）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/03_multi_agent_reference_driven_pipeline/run.py --workspace-root /tmp/srsdk-demo
```

预期：
- 运行成功退出（exit=0）
- stdout 含：`EXAMPLE_OK: workflows_03`
- workspace 下生成：`report.md`
- WAL 中出现：`tool_call_finished`（skill_ref_read/apply_patch/shell_exec/file_write）

