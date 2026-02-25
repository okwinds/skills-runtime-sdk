# auto_loop_research_assistant（多步研究助手 / Skills-First）

目标：提供“跑通感很强”的多步工作流体验（plan → tool → plan → report）：
- `request_user_input`：收集用户问题
- `update_plan`：每一步更新计划（让人看到推进过程）
- `grep_files` + `read_file`：在本地知识库中检索与读取
- `file_write`：输出 `report.md`

> 说明：这里的 “auto_loop” 是指多步循环式推进的体验形态；离线回归模式用 Fake backend 保证稳定。

## 1) 离线运行（默认，用于回归）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/auto_loop_research_assistant/run.py --workspace-root /tmp/srsdk-app-research --mode offline
```

预期：
- stdout 含：`EXAMPLE_OK: app_auto_loop_research_assistant`
- workspace 下生成：`kb.md`、`report.md`

## 2) 真模型运行（OpenAICompatible）

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"      # 可选
export SRS_MODEL_PLANNER="gpt-4o-mini"                  # 可选
export SRS_MODEL_EXECUTOR="gpt-4o-mini"                 # 可选

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/auto_loop_research_assistant/run.py --workspace-root /tmp/srsdk-app-research --mode real
```

