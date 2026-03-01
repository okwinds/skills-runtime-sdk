---
name: fastapi_sse_gateway_runner
description: "最小 FastAPI/SSE 网关骨架：create run → SSE stream → approvals decide → terminal event。"
metadata:
  short-description: "FastAPI/SSE：最小网关 + approvals decide 入口（离线可回归）。"
---

# fastapi_sse_gateway_runner（workflow / FastAPI SSE Gateway Minimal）

## 目标

演示一个“服务化/网关化”的最小落地形态：
- HTTP API 创建 run
- SSE 输出运行事件（event/data）
- 当出现 `approval_requested` 时，通过 decide API 写回审批决策
- 最终收到 terminal event（例如 `run_completed`）

## 输入约定

- 任务文本中应包含 mention：`$[examples:workflow].fastapi_sse_gateway_runner`（用于示例一致性）。
- 本示例的网关实现会把 message 作为事件 payload 的一部分写入 `events.jsonl`（用于审计）。

## 事件协议（最小）

SSE 消息格式（单条）：
- `event: <type>`
- `data: <one-line json>`
- 空行分隔

关键事件（示例最小集）：
- `approval_requested`：payload 包含 `approval_key`
- `approval_decided`：payload 包含 `approval_key` + `decision`
- `run_completed`：terminal

## 端点（最小）

- `POST /runs`：创建 run，返回 `run_id`
- `GET /runs/{run_id}/events/stream`：订阅 SSE
- `POST /runs/{run_id}/approvals/{approval_key}`：decide approvals

说明：以上为自定义网关示例端点（非 Studio 的 `/api/v1/...`）。

## 约束

- 默认离线：不访问外网、不依赖真实 key。
- 该示例的目的是“网关骨架与协议”，不是完整 run 引擎。
