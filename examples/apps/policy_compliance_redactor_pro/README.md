# policy_compliance_redactor_pro（合规扫描与脱敏闭环 / Skills-First）

目标：让人直观看到“读取 policy → 按规则打补丁 → 产物落盘”的闭环。

本示例强调：
- skills-first：任务中包含 `$[examples:app].*` mention，WAL 中会出现 `skill_injected`
- references：使用 `skill_ref_read` 读取 skill bundle 的 `references/policy.md`
- 合规补丁：`apply_patch` 只做**最小替换**（避免误伤）
- 产物：`patch.diff` / `result.md` / `report.md`

## 1) 离线运行（默认，用于回归）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/policy_compliance_redactor_pro/run.py --workspace-root /tmp/srsdk-app-policy --mode offline
```

预期：
- stdout 含：`EXAMPLE_OK: app_policy_compliance_redactor_pro`
- workspace 下生成：`target.md`、`patch.diff`、`result.md`、`report.md`

## 2) 真模型运行（OpenAICompatible）

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"      # 可选
export SRS_MODEL_PLANNER="gpt-4o-mini"                  # 可选
export SRS_MODEL_EXECUTOR="gpt-4o-mini"                 # 可选

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/policy_compliance_redactor_pro/run.py --workspace-root /tmp/srsdk-app-policy --mode real
```

说明：
- 真模型模式下，终端会提示审批（`apply_patch` / `file_write` 等写操作）。

