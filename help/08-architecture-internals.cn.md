<div align="center">

[中文](08-architecture-internals.cn.md) | [English](08-architecture-internals.md) | [Help](README.cn.md)

</div>

# 08. 机制详解：运行时内部如何协作

## 8.1 启动阶段（Bootstrap）

核心步骤：
1. 确定 `workspace_root`
2. 加载 `.env`（若存在）
3. 发现 overlay（`config/runtime.yaml` + env 指定路径）
4. 深度合并配置并做 pydantic 校验
5. 生成“有效配置来源追踪”（sources map）

价值：
- 能回答“这个字段到底从哪来”
- 降低 overlay 漂移导致的隐式故障

## 8.2 Agent Loop（简化）

```text
run_started
  -> compile prompt
  -> call LLM (stream)
  -> if tool_calls: orchestrate tools
      -> approval gate
      -> sandbox wrap
      -> execute tool
      -> inject tool result
  -> finish conditions
run_completed / run_failed / run_cancelled
```

## 8.3 事件日志（WAL）

运行事件落盘在：

```text
<workspace_root>/.skills_runtime_sdk/runs/<run_id>/events.jsonl
```

特点：
- append-only
- 兼容 SSE 转发
- 排障可重放

## 8.4 Prompt 组装机制

PromptManager 负责：
- system/developer 模板选择
- skills 注入（可开关）
- 历史滑窗裁剪（messages/chars）

## 8.5 Skills 机制关键点

- 发现阶段：metadata-only
- 注入阶段：按 mention 与预算限制读取 body
- mention 策略：自由文本容错 + 参数严格校验

## 8.6 Tool 编排关键点

- Registry：维护 ToolSpec 与 handler
- Gate：approval + policy
- Sandbox：restricted 时 wrapper 执行
- Result：统一 ToolResultPayload 回注

## 8.7 Safety 与 Sandbox 分层

- Safety 解决“是否允许执行”
- Sandbox 解决“允许后能执行到什么范围”

缺一不可。

## 8.8 Studio 与 SDK 结合点

- Studio 后端用 `_build_agent()` 构造 Agent
- `ApprovalHub` 将前端 decision 回填给 SDK 的审批等待点
- SSE 从 `events.jsonl` 转换输出

## 8.9 常见演进方向（看 backlog）

下面是“下一阶段常见会做但本期未做”的方向（仅供你评估扩展点）：

- 多 agent：跨进程/跨机器的 state 持久化与恢复
- 流式 tool 参数：delta 聚合与完整参数落定的状态机
- fork / resume：基于 `events.jsonl` 的逐事件重建与断点续跑
- Sandbox：profile/策略分阶段收紧 + 更强可观测性（失败归因更细）

## 8.10 Workspace runtime server（exec sessions / child agents）

用途：
- 托管“跨进程可复用”的 exec sessions（PTY + 子进程）
- 承载最小的 collab child agents（用于多 agent/并发雏形）

位置（workspace 级）：

```text
<workspace_root>/.skills_runtime_sdk/runtime/
  - runtime.sock                # Unix socket（JSON RPC）
  - server.json                 # pid/secret/socket_path/created_at_ms
  - server.stdout.log           # server 后台 stdout（便于排障）
  - server.stderr.log           # server 后台 stderr（便于排障）
  - exec_registry.json          # crash/restart orphan cleanup 注册表（pids + marker）
```

可观测接口（JSON RPC）：
- `runtime.status`：返回 server 健康与计数（active exec sessions / active children），并包含 registry 摘要
- `runtime.cleanup`：显式 stop/cleanup（关闭 exec sessions + 取消 children）

排障示例（离线）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 - <<'PY'
from pathlib import Path
from agent_sdk.runtime.client import RuntimeClient

ws = Path(".").resolve()
client = RuntimeClient(workspace_root=ws)
print(client.call(method="runtime.status"))
PY
```

---

上一章：[`07-studio-guide.cn.md`](./07-studio-guide.cn.md)  
下一章：[`09-troubleshooting.cn.md`](./09-troubleshooting.cn.md)
