# 20_policy_compliance_patch（Policy 合规补丁：references → patch → 产物）

本示例演示把“可分发政策/规则”随 Skill 一起打包（`references/`），并在运行期用 `skill_ref_read` 读取，然后对 workspace 文件打最小补丁（`apply_patch`）并产出可审计产物。

演示要点：
- Skills-First：任务文本包含 `$[examples:workflow].policy_compliance_patcher`，触发 `skill_injected` 证据事件；
- References：读取 `skills/policy_compliance_patcher/references/policy.md`（通过 `skill_ref_read`，默认 fail-closed，示例 overlay 显式开启）；
- Patch：对 `target.md` 执行 `apply_patch`（写操作走 approvals）；
- Artifacts：写入 `patch.diff` / `result.md` / `report.md`（写操作走 approvals）；
- Evidence：WAL（`events.jsonl`）可断言 `skill_injected`、`tool_call_finished(apply_patch)`、`approval_*`。

## 运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/20_policy_compliance_patch/run.py --workspace-root /tmp/srsdk-wf20
```

## 产物

- `target.md`：被修复的目标文件（示例会把“敏感 token”替换为 `[REDACTED]`）
- `patch.diff`：本次补丁（内容与 apply_patch 输入一致）
- `result.md`：修复结果摘要
- `report.md`：包含 `run_id`、`wal_locator`、关键断言点

