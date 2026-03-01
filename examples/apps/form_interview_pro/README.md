# form_interview_pro（表单访谈 Pro / Skills-First）

目标：演示一个“真实业务常见形态”的人类可用 demo：
- 结构化提问（`request_user_input`）
- 计划同步（`update_plan`）
- 产物落盘（`file_write`：`submission.json` + `report.md`）
- 最小确定性校验（`shell_exec`）
- Skills-First（任务包含 `$[examples:app].*` mentions；WAL 可审计）

## 1) 离线运行（默认，用于回归）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/form_interview_pro/run.py --workspace-root /tmp/srsdk-app-form --mode offline
```

预期：
- stdout 含：`EXAMPLE_OK: app_form_interview_pro`
- workspace 下生成：`submission.json`、`report.md`、`config/runtime.yaml`
- WAL（`events.jsonl`）中出现：`skill_injected`、`human_request/human_response`、`plan_updated`、`approval_*`

## 2) 真模型运行（OpenAICompatible）

准备环境变量：

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"          # 可选
export SRS_MODEL_PLANNER="gpt-4o-mini"                      # 可选
export SRS_MODEL_EXECUTOR="gpt-4o-mini"                     # 可选
```

运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/form_interview_pro/run.py --workspace-root /tmp/srsdk-app-form --mode real
```

说明：
- 真模型模式需要一个“支持 tool calling”的 chat model，否则可能无法按要求触发工具调用。
- 终端会提示审批（approvals）与结构化问答（HumanIOProvider）。
