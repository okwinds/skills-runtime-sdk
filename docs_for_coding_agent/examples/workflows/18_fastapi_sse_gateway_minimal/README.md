# 18_fastapi_sse_gateway_minimal（FastAPI + SSE 网关最小骨架）

本示例演示一个“本地网关”形态的最小实现：
- FastAPI server 提供 **创建 run**、**订阅 SSE events stream**、**approvals decide** 三类端点；
- `run.py` 作为离线回归脚本：启动 server（`127.0.0.1:<random_port>`）→ 创建 run → 订阅 SSE → 收到 `approval_requested` 后调用 approve → 等待 `run_completed` → 写 `report.md`。

> 说明：本 workflow 只演示 HTTP/SSE 网关骨架与事件协议，不依赖真实 LLM/外网。

## 运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 docs_for_coding_agent/examples/workflows/18_fastapi_sse_gateway_minimal/run.py --workspace-root /tmp/srsdk-wf18
```

依赖：
- 可选：`fastapi` + `uvicorn`
- 若依赖缺失，脚本会输出 `SKIPPED:` 原因，但仍打印 `EXAMPLE_OK: workflows_18`（避免离线门禁不稳定）。

## 产物

- `report.md`：包含 `run_id`、terminal event、`wal_locator` 与关键证据摘要
- `.skills_runtime_sdk/runs/<run_id>/events.jsonl`：最小事件记录（便于审计/排障）

## Skills-First（目录要求）

本目录仍提供一个最小 `SKILL.md`，用于表达“网关能力/事件协议”的可复用说明：
- `skills/fastapi_sse_gateway_runner/SKILL.md`
