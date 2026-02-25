# Applied Scenarios Workflows（18/20/22：离线可回归）

## Goal

补齐 3 个“Applied Scenarios”级别的 workflows 示例，使其满足：
- **离线可回归**（Fake backend / scripted providers 为默认路径）
- **可审计证据链**（WAL：`events.jsonl`，必要事件可断言）
- **可复刻的最小骨架**（README + run.py + skills/*/SKILL.md）

本页覆盖的 workflows：
- WF18：`docs_for_coding_agent/examples/workflows/18_fastapi_sse_gateway_minimal/`（FastAPI + SSE 网关最小形态）
- WF20：`docs_for_coding_agent/examples/workflows/20_policy_compliance_patch/`（policy 合规补丁：references → patch → artifacts）
- WF22：`docs_for_coding_agent/examples/workflows/22_chatops_incident_triage/`（ChatOps 排障：human I/O 澄清 → plan → runbook/report）

---

## Constraints

通用约束：
- 运行必须 **≤ 30s**（离线 smoke tests timeout=30）。
- 必须打印稳定标记：`EXAMPLE_OK: workflows_18/20/22`（供 smoke tests 断言）。
- 默认不依赖外网、不需要真实 key。
- 每个 workflow 目录必须包含：`README.md` + `run.py` + `skills/<skill>/SKILL.md`。

WF18 额外约束：
- 允许依赖 `fastapi`/`uvicorn`；若依赖缺失，`run.py` 必须 **明确 SKIP 原因**，但仍输出 `EXAMPLE_OK: workflows_18`（避免门禁不稳定）。

WF20 额外约束：
- policy 以可分发形态放在 skill bundle：`skills/<skill>/references/policy.md`。
- 运行期通过 `skill_ref_read` 读取（默认 fail-closed，overlay 需显式开启 `skills.references.enabled=true`）。

WF22 额外约束：
- 必须使用 `request_user_input`（离线：scripted `HumanIOProvider` 注入答案）。
- 必须使用 `update_plan` 推进任务（WAL 事件 `plan_updated`）。

---

## Contract（事件/证据口径）

WF18（HTTP + SSE）：
- SSE stream 输出 `event:` + `data:`（单行 JSON）+ 空行分隔。
- 至少包含：`approval_requested`、`approval_decided`、`run_completed`（或等价 terminal）。
- 产出 `report.md`，包含 `run_id`、terminal event、`wal_locator` 指针与关键证据摘要。

WF20（WAL 断言）：
- WAL 必须出现：
  - `skill_injected`（mention 为 `$[examples:workflow].<skill>`）
  - `tool_call_finished` 且 `tool=apply_patch` 且 `result.ok=true`
  - `approval_requested` 与 `approval_decided`（写操作的门卫证据）

WF22（WAL 断言）：
- WAL 必须出现：
  - `human_request` 与 `human_response`
  - `plan_updated`
  - 至少一个 `tool_call_finished` 且 `result.ok=true`（例如 `file_write`）

---

## Acceptance Criteria

1) 目录与文件存在：
- `docs_for_coding_agent/examples/workflows/18_fastapi_sse_gateway_minimal/README.md`
- `docs_for_coding_agent/examples/workflows/18_fastapi_sse_gateway_minimal/run.py`
- `docs_for_coding_agent/examples/workflows/18_fastapi_sse_gateway_minimal/skills/*/SKILL.md`
- `docs_for_coding_agent/examples/workflows/20_policy_compliance_patch/README.md`
- `docs_for_coding_agent/examples/workflows/20_policy_compliance_patch/run.py`
- `docs_for_coding_agent/examples/workflows/20_policy_compliance_patch/skills/*/SKILL.md`
- `docs_for_coding_agent/examples/workflows/20_policy_compliance_patch/skills/*/references/policy.md`
- `docs_for_coding_agent/examples/workflows/22_chatops_incident_triage/README.md`
- `docs_for_coding_agent/examples/workflows/22_chatops_incident_triage/run.py`
- `docs_for_coding_agent/examples/workflows/22_chatops_incident_triage/skills/*/SKILL.md`

2) 离线门禁覆盖：
- `packages/skills-runtime-sdk-python/tests/test_examples_smoke.py` 将 18/20/22 加入 smoke 列表；
- 跑 `python -m pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py` 通过。

3) 文档对齐：
- `docs_for_coding_agent/examples/workflows/README.md`、`docs_for_coding_agent/01-recipes.md`、`docs_for_coding_agent/03-workflows-guide.md`、
  `docs_for_coding_agent/capability-coverage-map.md` 增补 18/20/22 入口。

---

## Test Plan（离线回归）

最小回归命令（离线）：

```bash
./.venv/bin/python -m pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py
```

本地快速单跑（示例）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  ./.venv/bin/python docs_for_coding_agent/examples/workflows/20_policy_compliance_patch/run.py --workspace-root /tmp/srsdk-wf20
```
