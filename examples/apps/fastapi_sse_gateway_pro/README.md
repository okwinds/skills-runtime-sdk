# fastapi_sse_gateway_pro（FastAPI + SSE 网关 / Skills-First）

目标：提供一个“服务化”的人类示例，让人直观看到：
1) 启动网关服务（FastAPI + SSE）
2) 通过 HTTP 创建一次 run
3) 通过 SSE 订阅事件流（run_started / tool_calls / approvals / run_completed）
4) 通过 HTTP 审批写操作

> 重要说明：本示例暴露的是 **自定义网关 API（非 Studio MVP API）**，端点以 `/runs...` 开头（不带 `/api/v1` 前缀）。

### 对照：Studio MVP API（`/api/v1/...`）

Studio MVP 的官方端点口径以 `/api/v1/...` 为前缀（以及少量 `/studio/api/v1/...` 扩展），详见：`help/07-studio-guide.md`。

关键端点（节选）：
- `POST /api/v1/sessions`
- `PUT /api/v1/sessions/{session_id}/skills/sources`
- `GET /api/v1/sessions/{session_id}/skills`
- `POST /api/v1/sessions/{session_id}/runs`
- `GET /api/v1/runs/{run_id}/events/stream`
- `GET /api/v1/runs/{run_id}/approvals/pending`
- `POST /api/v1/runs/{run_id}/approvals/{approval_key}`

## 依赖

本示例会尝试使用 `fastapi` + `uvicorn`。
- 如果你环境里没有它们：请先安装（示例不修改框架依赖）。

```bash
python -m pip install fastapi uvicorn
```

## 1) 离线 Demo（默认，用于回归）

一个命令完成：启动服务 → 发起 run → 订阅 SSE → 自动批准审批 → 结束并退出。

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/fastapi_sse_gateway_pro/run.py --workspace-root /tmp/srsdk-app-sse --mode offline --demo
```

预期：
- stdout 含：`EXAMPLE_OK: app_fastapi_sse_gateway_pro`
- workspace 下出现 `runs/<run_id>/report.md`（run 独立子目录）

## 2) 真模型服务化运行（OpenAICompatible）

### 2.1 启动服务（终端 A）

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"      # 可选
export SRS_MODEL_PLANNER="gpt-4o-mini"                  # 可选
export SRS_MODEL_EXECUTOR="gpt-4o-mini"                 # 可选

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/apps/fastapi_sse_gateway_pro/run.py --workspace-root /tmp/srsdk-app-sse --mode real --serve --port 8000
```

### 2.2 创建 run（终端 B）

```bash
curl -s -X POST http://127.0.0.1:8000/runs \
  -H 'Content-Type: application/json' \
  -d '{"message":"生成一份简短的运行报告并落盘"}'
```

返回里会包含 `run_id`。

### 2.3 订阅 SSE（终端 B）

```bash
curl -N http://127.0.0.1:8000/runs/<run_id>/events/stream
```

当你看到 `approval_requested` 后，在终端 C 执行审批：

```bash
curl -s -X POST http://127.0.0.1:8000/runs/<run_id>/approvals/<approval_key> \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve"}'
```

说明：
- `decision` 兼容别名：`approve` / `approved` / `y` / `yes`。
