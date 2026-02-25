# data_import_validate_and_fix（数据导入校验与修复 / Skills-First）

目标：模拟一次常见的数据导入任务：
- 输入：`input.csv`（包含缺失/非法值）
- 输出：`fixed.csv` + `validation_report.json` + `report.md`

演示能力点：
- `read_file` / `file_write`
- `shell_exec` 最小 QA（确定性校验）
- Skills-First（mentions + WAL）

## 1) 离线运行（默认，用于回归）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/data_import_validate_and_fix/run.py --workspace-root /tmp/srsdk-app-data --mode offline
```

预期：
- stdout 含：`EXAMPLE_OK: app_data_import_validate_and_fix`
- workspace 下生成：`input.csv`、`fixed.csv`、`validation_report.json`、`report.md`

## 2) 真模型运行（OpenAICompatible）

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"      # 可选
export SRS_MODEL_PLANNER="gpt-4o-mini"                  # 可选
export SRS_MODEL_EXECUTOR="gpt-4o-mini"                 # 可选

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/data_import_validate_and_fix/run.py --workspace-root /tmp/srsdk-app-data --mode real
```

