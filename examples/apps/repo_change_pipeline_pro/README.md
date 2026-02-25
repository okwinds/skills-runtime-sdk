# repo_change_pipeline_pro（Repo 变更流水线 / Skills-First）

目标：让人直观看到“Analyze → Patch → QA → Report”的工程闭环（偏真实 repo 工作流的形态）。

本示例会在 workspace 里创建一个最小项目：
- `app.py`：包含一个明显 bug（`is_even` 判断写反）
- `test_app.py`：pytest 用例（预期修复后通过）

## 1) 离线运行（默认，用于回归）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/repo_change_pipeline_pro/run.py --workspace-root /tmp/srsdk-app-repo --mode offline
```

预期：
- stdout 含：`EXAMPLE_OK: app_repo_change_pipeline_pro`
- workspace 下生成：`app.py`、`test_app.py`、`patch.diff`、`report.md`

## 2) 真模型运行（OpenAICompatible）

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"      # 可选
export SRS_MODEL_PLANNER="gpt-4o-mini"                  # 可选
export SRS_MODEL_EXECUTOR="gpt-4o-mini"                 # 可选

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/repo_change_pipeline_pro/run.py --workspace-root /tmp/srsdk-app-repo --mode real
```

说明：
- 真模型模式下，终端会提示审批（写文件/打补丁/跑命令）。
- 建议先跑一次 `--mode offline` 看看产物结构，再切到 `--mode real`。

