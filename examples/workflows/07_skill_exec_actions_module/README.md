# 07_skill_exec_actions_module（Skill Actions：skill_exec 执行动作 / Skills-First）

本示例演示如何把“可执行动作”打包到 Skill bundle 里，并通过 builtin tool `skill_exec` 受控执行：

- 动作定义在 `SKILL.md` frontmatter 的 `actions` 下
- 动作脚本必须放在 `actions/` 目录内（路径逃逸会被拒绝）
- 运行期需要显式开启：`skills.actions.enabled=true`（默认 fail-closed）
- `skill_exec` 本身走 approvals/sandbox/WAL 证据链（与 `shell_exec` 对齐）

本示例默认离线可回归（不依赖外网/真实 key）。

## 如何运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/07_skill_exec_actions_module/run.py --workspace-root /tmp/srsdk-wf07
```

你将看到：
- `action_artifact.json`：由 action 脚本生成的产物
- `report.md`：包含 skill_exec 的 wal_locator 指针与产物摘要
- `EXAMPLE_OK: workflows_07`
