# 19_view_image_offline（离线示例：view_image）

## 场景

在 **离线可回归**（不依赖外网/真实 key）的约束下，演示内置工具 `view_image` 的最小落地形态：

1) 脚本运行时在 `--workspace-root` 下生成一个极小的 PNG（`generated.png`）  
2) 通过 **skills-first** 的 agent（任务文本包含 skill mention）触发 `view_image` 读取图片并返回 base64/mime/bytes  
3) 通过 `file_write` 落盘：
   - `image_meta.json`：图片元信息（含 sha256 与预期 mime/bytes）
   - `report.md`：汇总报告（含 `events_path` 证据指针）

## 边界与约束

- 仅演示 `view_image` 的 **成功路径**（PNG，且路径在 workspace 内）。
- 不引入 Pillow 等重型依赖：PNG bytes 由脚本内嵌 base64 解码得到。
- 所有产物必须写入 `--workspace-root`（不写入仓库目录）。

## 如何运行（离线）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/19_view_image_offline/run.py \
  --workspace-root /tmp/srsdk-wf19
```

预期 stdout 包含稳定标记：

```text
EXAMPLE_OK: workflows_19
```

## 预期产物（workspace 内）

- `generated.png`：运行时生成的小 PNG（二进制）
- `image_meta.json`：元信息（JSON；包含 sha256/mime/bytes/base64）
- `report.md`：汇总报告（Markdown）
- `runtime.yaml`：示例运行 overlay（skills root / safety mode 等）

## 预期 WAL 证据（离线门禁可审计）

WAL 路径会写在 `report.md` 中（字段 `events_path`）。你也可以按约定位置查找：

- `<workspace_root>/.skills_runtime_sdk/runs/<run_id>/events.jsonl`

其中应至少包含：

- `type=skill_injected` 且 `payload.mention_text==$[examples:workflow].view_image_offline_runner`
- `type=tool_call_finished` 且 `payload.tool=view_image` 且 `payload.result.ok=true`
- `type=tool_call_finished` 且 `payload.tool=file_write` 且 `payload.result.ok=true`

## 离线 smoke tests（门禁）

本示例纳入离线 smoke tests：

- `pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py -k workflows_19`

