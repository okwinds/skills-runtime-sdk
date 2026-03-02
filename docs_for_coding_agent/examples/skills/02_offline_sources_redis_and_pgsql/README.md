# 02_offline_sources_redis_and_pgsql（离线：redis/pgsql sources 的 scan + inject 契约）

目的：
- 给出一个**完全离线可回归**的示例，演示 `SkillsManager(..., source_clients=...)` 注入 fake redis/pgsql client 后：
  - `scan()` 只读取 **metadata**（不读取 body）
  - `render_injected_skill(...)`（inject）才会触发 body 读取

关键契约（本示例验证的点）：
- scan（metadata-only）：
  - Redis：只会 `SCAN`/`HGETALL` meta key，不会 `GET body_key`
  - PgSQL：只会执行“扫描行”的查询，不会执行“读取 body”的回表查询
- inject（lazy body loader）：
  - Redis：inject 时才 `GET body_key`
  - PgSQL：inject 时才按 `row_id + namespace` 回表读取 `body`

如何离线运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/skills/02_offline_sources_redis_and_pgsql/run.py \
  --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: skills_sources_redis_pgsql_offline`

