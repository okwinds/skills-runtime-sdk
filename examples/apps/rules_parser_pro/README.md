# rules_parser_pro（规则解析 Pro / Skills-First）

目标：把“业务自然语言规则”落为可审计产物：
- `plan.json`：可执行/可审计的结构化计划（plan）
- `result.json`：执行结果（示例中为简化直接给出结果）
- `report.md`：人类可读报告

同时演示：
- `request_user_input`：向用户收集 `code_string` 与规则文本
- `update_plan`：同步执行进度
- `file_write`：落盘产物
- `shell_exec`：最小确定性 QA（断言 result.json 结构）

## 1) 离线运行（默认，用于回归）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/rules_parser_pro/run.py --workspace-root /tmp/srsdk-app-rules --mode offline
```

预期：
- stdout 含：`EXAMPLE_OK: app_rules_parser_pro`
- workspace 下生成：`plan.json`、`result.json`、`report.md`、`config/runtime.yaml`

## 2) 真模型运行（OpenAICompatible）

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"      # 可选
export SRS_MODEL_PLANNER="gpt-4o-mini"                  # 可选
export SRS_MODEL_EXECUTOR="gpt-4o-mini"                 # 可选

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/rules_parser_pro/run.py --workspace-root /tmp/srsdk-app-rules --mode real
```

说明：
- 真模型模式要求模型具备 tool calling 能力，才能按要求写文件/跑 QA。
