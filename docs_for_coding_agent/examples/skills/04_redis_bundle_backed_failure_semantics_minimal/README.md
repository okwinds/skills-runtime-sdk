# 04_redis_bundle_backed_failure_semantics_minimal（离线：Redis bundle-backed 的 fail-closed 失败语义）

目的：
- 提供一个**最小可复刻**的离线脚本，专门覆盖“失败语义/边界（fail-closed）”：
  - 非法 zip entry（zip slip / 非预期顶层目录）
  - 非法 `ref_path`（越界/绝对路径/包含 `..`）
  - 非法 action `argv`（路径逃逸：绝对路径或不在 `actions/` 下）

本示例覆盖的 fail-closed 分支（稳定错误码）：
- A) 非法 zip entry → `SKILL_BUNDLE_INVALID`
  - 可选：额外断言 `data.error.details.reason`（例如 `dotdot_segment`、`unexpected_top_level`）
- B) 非法 `ref_path` → `SKILL_REF_PATH_INVALID` 且 `error_kind=permission`
- C) 非法 action argv → `SKILL_ACTION_ARGV_PATH_ESCAPE` 且 `error_kind=permission`

推荐断言字段（避免 brittle）：
- `result.error_kind`
- `result.details["data"]["error"]["code"]`
- （可选）`result.details["data"]["error"]["details"]["reason"]`（只对 bundle zip 校验分支适用）

如何离线运行：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/skills/04_redis_bundle_backed_failure_semantics_minimal/run.py \
  --workspace-root /tmp/srsdk-demo
```

预期输出（关键标记）：
- `EXAMPLE_OK: redis_bundle_backed_failure_semantics`

