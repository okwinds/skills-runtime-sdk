<div align="center">

[中文](08-architecture-internals.cn.md) | [English](08-architecture-internals.md) | [Help](README.cn.md)

</div>

# 08. 机制详解：运行时内部如何协作

本章从“机制/骨架”的视角解释 SDK：组件边界、谁调用谁、以及状态/产物落在什么地方。

如果你更关心 **tools/safety/sandbox 的心智模型**，建议先读：
- `help/06-tools-and-safety.cn.md`
- `help/14-safety-deep-dive.cn.md`

如果你要查配置字段含义，读：
- `help/02-config-reference.cn.md`

## 8.0 架构总览（At a glance）

SDK 有意分成三层（降低隐式行为、提升可复现性）：

1) **Bootstrap 层（可选）**：发现 overlays + 加载 `.env` + 产出“字段来源追踪”
2) **Core Agent/Runtime 层**：确定性的 agent loop + 事件流 + 工具编排
3) **Workspace runtime server（可选）**：一个很小的本地 JSON-RPC server，用于长驻 exec session / child agents

```text
                            （可选）
┌──────────────────────────────────────────────────────────────────────┐
│ Bootstrap 层                                                         │
│  - load_dotenv_if_present()                                          │
│  - discover_overlay_paths()                                          │
│  - resolve_effective_run_config() -> sources map                     │
│  code: packages/.../skills_runtime/bootstrap.py                       │
└──────────────────────────────────────────────────────────────────────┘
                      │（effective config / paths / env）
                      v
┌──────────────────────────────────────────────────────────────────────┐
│ Core（进程内）                                                       │
│  Agent（对外入口）                                                   │
│   - PromptManager（模板 + skills + history trim）                     │
│   - ToolDispatcher/ToolRegistry（校验 -> gate -> exec -> result）     │
│   - WAL（events.jsonl）+ WalEmitter（append -> hooks -> stream）      │
│  code: packages/.../skills_runtime/core/agent.py                      │
└──────────────────────────────────────────────────────────────────────┘
                      │（可选）
                      v
┌──────────────────────────────────────────────────────────────────────┐
│ Workspace runtime server（进程外，workspace 级单例）                   │
│  - exec sessions：PTY-backed 的长驻子进程                              │
│  - child agents：最小并发原语（早期多 agent/并发积木）                 │
│  code: packages/.../skills_runtime/runtime/{client.py,server.py}      │
└──────────────────────────────────────────────────────────────────────┘
```

文件系统产物布局（filesystem WAL backend）：

```text
<workspace_root>/
  .skills_runtime_sdk/
    runs/<run_id>/events.jsonl
    runs/<run_id>/artifacts/...
    runtime/
      runtime.sock
      server.json
      server.stdout.log
      server.stderr.log
      exec_registry.json
```

## 8.1 启动阶段（Bootstrap：配置发现 + 来源追踪）

核心步骤：
1. 确定 `workspace_root`
2.（可选）加载 `.env`（若存在）
3. 发现 overlay YAML（顺序稳定）
4. 深度合并 dict 并做 Pydantic 校验（未知字段 fail-fast）
5. 生成“有效配置来源追踪”（sources map）

价值：
- 能回答“这个字段到底从哪来”
- 降低 overlay 漂移导致的隐式故障

### 8.1.1 为什么 Bootstrap 单独成模块

**核心 `Agent` 构造函数刻意保持“无隐式 I/O”**：
- 不会自动读 `.env`
- 不会自动发现 overlay 文件

这样做的目的：可复现、可解释。`Agent(...)` 的行为应该只由显式输入决定
（`workspace_root` / `config_paths` / 注入的 `env_vars` 等）。

Bootstrap 是给 CLI / Web 应用复用的“开箱即用层”：
- 代码：`packages/skills-runtime-sdk-python/src/skills_runtime/bootstrap.py`

### 8.1.2 Overlay 的发现顺序

Overlay 发现规则固定、顺序稳定：

1) `<workspace_root>/config/runtime.yaml`（若存在）
2) `SKILLS_RUNTIME_SDK_CONFIG_PATHS`（逗号/分号分隔）

对应实现：
- `discover_overlay_paths(workspace_root=...)`

### 8.1.3 “深度合并”到底怎么合

为了避免过拟合与不可控，合并规则保持简单（见
`packages/skills-runtime-sdk-python/src/skills_runtime/config/loader.py` 的 `_deep_merge`）：

- `dict + dict`：递归合并
- `list`：整体覆盖（不做拼接/去重）
- 其它类型：overlay 直接覆盖 base

```text
base:
  tools:
    allowlist: ["git", "rg"]
overlay:
  tools:
    allowlist: ["git"]     # 整体覆盖
effective:
  tools:
    allowlist: ["git"]
```

### 8.1.4 来源追踪（sources map）

Bootstrap 可以为关键字段产出“来源追踪”（session > env > yaml）：

```text
models.executor -> env:SKILLS_RUNTIME_SDK_EXECUTOR_MODEL
llm.base_url    -> yaml:overlay:/.../config/runtime.yaml#llm.base_url
```

对应实现：
- `resolve_effective_run_config(workspace_root=..., session_settings=...)`

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

### 8.2.1 真实 loop：职责与不变量

Agent loop 除了“调模型 + 执行工具”之外，还必须保证：
- step/wall-time 预算约束（`LoopController`）
- 关键转移都写 WAL（可排障 + 可回放）
- context-recovery（例如 compaction turn）可控且可回归
- approvals 与 tool 事件的相对顺序稳定（否则 UI 容易漂移）

关键模块：
- `packages/skills-runtime-sdk-python/src/skills_runtime/core/agent.py`
- `packages/skills-runtime-sdk-python/src/skills_runtime/core/loop_controller.py`

### 8.2.2 时序图（LLM + tools + approvals + WAL）

```text
Caller/UI             Agent                LLM backend        ToolRegistry/Handlers
   |                   |                      |                   |
   | run_stream()      |                      |                   |
   |------------------>|                      |                   |
   |                   | run_started（WAL）    |                   |
   |                   |--------------------->|（emit）           |
   |                   | build prompt         |                   |
   |                   | call stream_chat()   |                   |
   |                   |--------------------->|                   |
   |                   | llm_response_delta   |                   |
   | events（yield）    |<---------------------|                   |
   |<------------------|                      |                   |
   |                   | tool_call_requested  |                   |
   |                   | approval gate?       |                   |
   |                   |  - policy eval       |                   |
   |                   |  - ApprovalProvider  |                   |
   |                   | tool_call_started    |                   |
   |                   | dispatch(call)       |                   |
   |                   |----------------------------------------->|
   |                   | tool_call_finished   |                   |
   |                   | inject tool msg      |                   |
   |                   | loop / finish        |                   |
   |                   | run_completed（WAL）  |                   |
   |<------------------|                      |                   |
```

备注：
- 工具参数在写入 WAL 前会做脱敏/裁剪（例如 env 只落 keys；file_write.content → sha256）。
- 某些 tool 相关事件会走 `WalEmitter.append(...)`，用来保证 approvals/event 序列严格可控。

## 8.3 事件日志（WAL）

运行事件落盘在：

```text
<workspace_root>/.skills_runtime_sdk/runs/<run_id>/events.jsonl
```

特点：
- append-only
- 兼容 SSE 转发
- 排障可重放

### 8.3.1 “事件”是什么

事件是一个 `AgentEvent` 对象，以“一行 JSON”的形式写入 WAL。常见类型：
- `run_started`, `prompt_compiled`
- `llm_response_delta` / `llm_response_completed`
- `tool_call_requested`, `tool_call_started`, `tool_call_finished`
- 终态：`run_completed`, `run_failed`, `run_cancelled`

选择 JSONL 的原因：
- 适合流式追加与 tail
- 抗部分写入损坏（通常只影响最后一行）
- 低成本检索（`rg`/`jq`）

### 8.3.2 `wal_locator` 与行号（line index）

对文件系统 WAL backend 而言，`wal_locator` 的语义就是 WAL 文件的绝对路径字符串。

内部约定：
- `JsonlWal.append(...)` 返回 **0-based 行号**
- 行号是后续 replay/fork 的稳定位置（逐步演进能力）

代码：
- `packages/skills-runtime-sdk-python/src/skills_runtime/state/jsonl_wal.py`

### 8.3.3 统一事件出口（`WalEmitter`）

为了保证事件顺序一致，核心会通过一个统一管道输出：

```text
WalEmitter.emit(event)
  -> wal.append(event)      # 先落盘（durable）
  -> hooks(event)           # 可观测性（fail-open）
  -> stream(event)          # 推给调用方（UI/CLI）
```

代码：
- `packages/skills-runtime-sdk-python/src/skills_runtime/state/wal_emitter.py`

## 8.4 Prompt 组装机制

PromptManager 负责：
- system/developer 模板选择
- skills 注入（可开关）
- 历史滑窗裁剪（messages/chars）

### 8.4.1 固定注入顺序（为什么重要）

Prompt 的注入顺序是固定的，这样你才能定位“prompt 为何变了”（减少漂移）：

1) system template
2) developer policy（为了兼容 chat.completions，会合并到 system）
3) skills list section（可选）
4) injected skill bodies（你请求注入的 skill 正文）
5) conversation history（滑窗裁剪后）
6) current user task / user input

```text
┌─────────────┐
│ system      │  <- 含 “[Developer Policy] ...”
└─────────────┘
┌─────────────┐
│ user        │  <- 可选 “Available skills ...”
└─────────────┘
┌─────────────┐
│ user        │  <- 注入 skill A
├─────────────┤
│ user        │  <- 注入 skill B
└─────────────┘
┌─────────────┐
│ history...  │  <- 裁剪后的对话历史
└─────────────┘
┌─────────────┐
│ user        │  <- 当前任务
└─────────────┘
```

代码：
- `packages/skills-runtime-sdk-python/src/skills_runtime/prompts/manager.py`

## 8.5 Skills 机制关键点

- 发现阶段：metadata-only
- 注入阶段：按 mention 与预算限制读取 body
- mention 策略：自由文本容错 + 参数严格校验

### 8.5.1 核心概念：space/source/namespace

Skills 的组织方式：
- **namespace**：合法的 slug 链，例如 `team:product:agent`
- **space**：配置单元，把 namespace 绑定到一组 sources
- **source**：skill 的来源（filesystem / in-memory / redis / pgsql）

```text
skills/
  spaces[]:  (namespace + enabled + sources[])
  sources[]: (id + type + connection/config)
```

代码：
- `packages/skills-runtime-sdk-python/src/skills_runtime/skills/manager.py`
- `packages/skills-runtime-sdk-python/src/skills_runtime/skills/mentions.py`

### 8.5.2 scan vs inject（元数据 vs 正文）

为了让冷启动更快，技能分两步：

```text
scan():   只读 frontmatter/metadata -> 建索引（不读正文）
inject(): 只为被提到的 skills 读取 SKILL.md 正文（受预算控制）
```

这样既能快速列出可用 skills，又能控制注入到模型的文本量。

### 8.5.3 刷新策略与缓存

scan 可配置 refresh_policy：
- `always`（每次强制 rescan）
- `ttl`（超过 `ttl_sec` 才 rescan）
- `manual`（只在显式调用 scan 时进行）

交互式使用更快，CI/校验也能保持确定性。

## 8.6 Tool 编排关键点

- Registry：维护 ToolSpec 与 handler
- Gate：approval + policy
- Sandbox：restricted 时 wrapper 执行
- Result：统一 ToolResultPayload 回注

### 8.6.1 两个平面：协议 vs 执行

工具系统通常分两块：

1) **协议**：`ToolSpec` 是模型能看到的（name + JSON schema）
2) **执行**：handler 在 `ToolExecutionContext` 中运行，产出 `ToolResult`

```text
ToolSpec -> LLM tool_calls -> ToolCall(args) -> validate -> handler -> ToolResult
```

代码：
- `packages/skills-runtime-sdk-python/src/skills_runtime/tools/protocol.py`
- `packages/skills-runtime-sdk-python/src/skills_runtime/tools/registry.py`

### 8.6.2 ExecutionContext 也是安全边界的一部分

`ToolExecutionContext` 提供一组框架级护栏：
- `resolve_path(...)` 禁止访问 `workspace_root` 之外的路径
- env 合并采用 `ctx.env`（session store）+ per-call 覆盖（事件里只记录 keys，不记录 values）
- 事件落盘前对参数做脱敏/裁剪（降低 secrets 回显风险）

即使某个 handler 写法不严谨，这些护栏也能减少爆炸半径。

## 8.7 Safety 与 Sandbox 分层

- Safety 解决“是否允许执行”
- Sandbox 解决“允许后能执行到什么范围”

缺一不可。

```text
        决策平面                      执行平面
┌──────────────────────────┐   ┌──────────────────────────────────────┐
│ Safety / Policy / Approvals│  │ OS sandbox（seatbelt/bwrap/none）     │
│  - allow/ask/deny          │  │  - 文件/网络等约束                    │
│  - allowlist/denylist      │  │  - 进程隔离                           │
└──────────────────────────┘   └──────────────────────────────────────┘
```

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

### 8.10.1 为什么要单独起一个进程

有些能力很难纯进程内优雅实现：
- 长驻 PTY session 需要跨多次 tool call 复用
- 并发 child agents 不能阻塞主 loop

因此 SDK 使用 workspace 级单例 server：
- 按需启动（`RuntimeClient.ensure_server()`）
- 通过 `server.json` 里的本地 secret 做鉴权
- idle 超时自动退出（避免测试/脚本留下后台僵尸进程）

代码：
- `packages/skills-runtime-sdk-python/src/skills_runtime/runtime/client.py`
- `packages/skills-runtime-sdk-python/src/skills_runtime/runtime/server.py`

### 8.10.2 JSON-RPC 形态（简化）

这不是公网 API，只是本地 Unix socket 协议。

```json
{"method":"runtime.status","params":{},"secret":"<from server.json>"}
```

排障示例（离线）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 - <<'PY'
from pathlib import Path
from skills_runtime.runtime.client import RuntimeClient

ws = Path(".").resolve()
client = RuntimeClient(workspace_root=ws)
print(client.call(method="runtime.status"))
PY
```

---

上一章：[`07-studio-guide.cn.md`](./07-studio-guide.cn.md)  
下一章：[`09-troubleshooting.cn.md`](./09-troubleshooting.cn.md)
