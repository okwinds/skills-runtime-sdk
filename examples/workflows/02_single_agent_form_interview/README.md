# 02_single_agent_form_interview（单 Agent：多轮表单访谈 / Skills-First）

本示例演示一个“真实业务常见形态”的单 agent 工作流：多轮采集结构化信息（表单访谈），并落盘产物与证据链。

流程（单 agent 内完成）：
1. **Interview**：通过 `request_user_input` 向用户逐题收集字段（离线：用 scripted HumanIOProvider 注入答案）
2. **Plan**：用 `update_plan` 写入结构化进度（`plan_updated` 事件）
3. **Persist**：用 `file_write` 写 `submission.json`
4. **QA**：用 `shell_exec` 做最小确定性校验（解析 JSON 并打印 `FORM_OK`）

关键约束：**skills-first** —— 角色能力来自 `skills/*/SKILL.md`，任务文本包含对应 mention，WAL 中必须出现 `skill_injected`。

## 如何运行（离线）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/02_single_agent_form_interview/run.py --workspace-root /tmp/srsdk-demo
```

预期：
- 运行成功退出（exit=0）
- stdout 含：`EXAMPLE_OK: workflows_02`
- workspace 下生成：`submission.json`
- WAL 中出现：`human_request/human_response`、`plan_updated`、`skill_injected`、`approval_*`（file_write/shell_exec）

