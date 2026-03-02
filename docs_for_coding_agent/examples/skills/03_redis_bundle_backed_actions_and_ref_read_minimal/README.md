# 03_redis_bundle_backed_actions_and_ref_read_minimal（离线：Redis bundle-backed 的 actions + ref-read）

目的：
- 给出一个**最小可复刻**的 bundle-backed 示例：skill 的 actions/ref-read 不依赖本地 filesystem skill，而是从 Redis（此处用 fake redis）取回 zip bundle，并按 `bundle_sha256` 做缓存复用。

bundle zip 结构（本示例最小集合）：
- `actions/`：可执行动作脚本（`skill_exec` 只能从这里 materialize 路径）
- `references/`：可读引用材料（`skill_ref_read` 默认只允许读取这里）

关键边界（本示例解释的点）：
- `references/` 与 `actions/` 是两条不同能力路径：
  - `skill_ref_read` 只读受限目录（默认 `references/`；本示例不启用 `assets/`）
  - `skill_exec` 只执行 SKILL 元数据（frontmatter.actions）中声明的 action
- `bundle_sha256` 是内容指纹（cache key）：
  - 第一次 ref-read 会触发 bundle `GET` 并解压到缓存目录
  - 第二次 ref-read 必须复用缓存，不再触发第二次 bundle `GET`

如何离线运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/skills/03_redis_bundle_backed_actions_and_ref_read_minimal/run.py \
  --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: redis_bundle_backed_actions_ref_read`

