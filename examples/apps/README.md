# examples/apps（面向人类的应用示例）

本目录的示例目标是“跑起来像小应用”：
- 有交互（终端问答 / 审批提示）
- 有过程感（run_stream 事件 + tool_calls + approvals）
- 有产物（workspace 下生成 report / json / csv / diff 等）
- 同时提供：
  - 离线可回归（默认，Fake backend）
  - 真模型可跑（OpenAICompatible，需你本地配置 key）

## 环境变量（真模型）

建议使用 `.env` 或直接导出环境变量（不要提交真实密钥）：

- `OPENAI_API_KEY`：必填
- `OPENAI_BASE_URL`：可选，默认 `https://api.openai.com/v1`
- `SRS_MODEL_PLANNER`：可选，默认 `gpt-4o-mini`
- `SRS_MODEL_EXECUTOR`：可选，默认 `gpt-4o-mini`

示例：  

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"
export SRS_MODEL_PLANNER="gpt-4o-mini"
export SRS_MODEL_EXECUTOR="gpt-4o-mini"
```

## 运行约定

所有 app 都支持：
- `--workspace-root <path>`：工作区目录（产物输出位置）
- `--mode offline|real`：离线或真模型

默认 `--mode offline` 用于离线回归门禁；你手动体验时建议使用 `--mode real`。

## App 列表

- `form_interview_pro/`：表单访谈 Pro（request_user_input + update_plan + file_write + shell_exec）。
- `rules_parser_pro/`：规则解析 Pro（自然语言规则→plan/result/report；强调确定性 QA）。
- `incident_triage_assistant/`：排障助手（read_file + request_user_input + runbook/report）。
- `repo_change_pipeline_pro/`：Repo 变更流水线（Analyze → Patch → QA → Report；更贴近工程闭环）。
- `ci_failure_triage_and_fix/`：CI 失败排障与修复闭环（pytest 失败→apply_patch 修复→pytest 通过→report）。
- `data_import_validate_and_fix/`：数据导入校验与修复（csv→修复→校验→报告）。
- `auto_loop_research_assistant/`：多步研究助手（update_plan→检索/读取→再计划→汇总报告）。
- `policy_compliance_redactor_pro/`：合规扫描与脱敏闭环（skill_ref_read(policy) → apply_patch → artifacts）。
- `fastapi_sse_gateway_pro/`：FastAPI + SSE 网关（服务化 run + SSE 订阅 + HTTP 审批）。
