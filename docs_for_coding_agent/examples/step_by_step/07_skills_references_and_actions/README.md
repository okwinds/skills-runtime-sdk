# 07_skills_references_and_actions（skills references + actions）

目标：
- 演示 Skills（explicit spaces/sources）下，如何：
  - `skill_ref_read` 读取 `references/` 引用材料（默认 fail-closed；需显式开启）
  - `skill_exec` 执行 `SKILL.md` frontmatter.actions 定义的动作（filesystem-only）
- 演示 skill_exec 在安全层面的等价性：会走 approvals gate（与 shell_exec 同等级风险）

运行（离线，可回归）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/step_by_step/07_skills_references_and_actions/run.py --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: step_by_step_07`
