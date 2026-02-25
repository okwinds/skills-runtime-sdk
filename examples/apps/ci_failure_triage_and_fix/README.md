# ci_failure_triage_and_fix（CI 失败排障与修复闭环 / Skills-First）

目标：让人直观看到“工程交付闭环”：
1. 写入一个最小项目（`app.py` + `test_app.py`）
2. 跑一次 `pytest`（预期失败，模拟 CI 失败）
3. `apply_patch` 做最小修复
4. 再跑一次 `pytest`（预期通过）
5. 输出 `report.md`（记录结论与产物）

## 1) 离线运行（默认，用于回归）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/ci_failure_triage_and_fix/run.py --workspace-root /tmp/srsdk-app-ci --mode offline
```

预期：
- stdout 含：`EXAMPLE_OK: app_ci_failure_triage_and_fix`
- workspace 下生成：`app.py`、`test_app.py`、`report.md`

## 2) 真模型运行（OpenAICompatible）

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"      # 可选
export SRS_MODEL_PLANNER="gpt-4o-mini"                  # 可选
export SRS_MODEL_EXECUTOR="gpt-4o-mini"                 # 可选

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/ci_failure_triage_and_fix/run.py --workspace-root /tmp/srsdk-app-ci --mode real
```

说明：
- 真模型模式下，终端会提示审批（写文件/打补丁/跑命令）。

