# 01_skills_preflight_and_scan（最小 skills preflight + scan）

目标：
- 构造最小 `skills.spaces/sources` 配置
- 创建一个最小 `SKILL.md`
- 调用 `SkillsManager.preflight()` 与 `SkillsManager.scan()`

运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/skills/01_skills_preflight_and_scan/run.py --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: skills_preflight_scan`

