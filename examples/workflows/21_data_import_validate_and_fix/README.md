# 21_data_import_validate_and_fix（数据导入校验与修复 / Skills-First / 离线可回归）

本示例演示一个“落地场景”：对导入数据做校验并自动修复，产出**可审计证据链**（WAL）与**确定性**可回归产物。

目标：
- 输入：`input.csv`（故意包含错误：缺 email、age 非整数、重复 id）
- 输出：
  - `fixed.csv`：修复后的可导入数据（规则确定性、可回归）
  - `validation_report.json`：校验与修复报告（结构稳定）
  - `report.md`：产物指针与结论（QA 结果见 WAL）

技能（skills-first）：
- 任务文本包含 skill mention：`$[examples:workflow].data_import_fixer`
- WAL（`events.jsonl`）中会出现 `skill_injected` 证据事件

## 约束与边界

- **离线可回归**：使用 `FakeChatBackend` + scripted approvals；不依赖外网与真实 key
- **确定性**：输入与修复规则固定；QA 校验为 `python -c` 的确定性断言
- **写入范围**：所有产物写入 `--workspace-root` 指定的 workspace 内
- **证据链**：运行过程写入 WAL：`<workspace>/.skills_runtime_sdk/runs/<run_id>/events.jsonl`

## 如何运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/21_data_import_validate_and_fix/run.py --workspace-root /tmp/srsdk-wf21
```

## 预期产物

在 `--workspace-root` 指定目录下：
- `input.csv`（脚本生成的带错误输入）
- `fixed.csv`（修复后输出）
- `validation_report.json`（校验/修复报告）
- `report.md`（汇总报告）
- `.skills_runtime_sdk/runs/<run_id>/events.jsonl`（WAL 证据）

## 离线门禁（建议）

本仓库有 smoke tests 覆盖该示例；本地可跑：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src .venv/bin/python -m pytest -q \
  packages/skills-runtime-sdk-python/tests/test_examples_smoke.py -k workflows_21
```

