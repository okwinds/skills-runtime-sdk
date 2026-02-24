# Capability Inventory（SDK 全能力点清单）

> 约定：CAP-* 是“能力点”，不是 backlog。  
> OSS 版本不保证包含内部 backlog/台账；本清单只描述“公开可复现”的能力与证据入口。

本清单目标：做到“不遗漏”，便于编码智能体快速确认：
- 当前 SDK 已具备哪些能力（可回归/可复刻）
- 每个能力点对应的契约入口（help/源码）、示例（examples）、回归（tests）

---

## CAP-SDK-*（核心能力域）

### CAP-SDK-001：配置与 overlay（Config Loader）

- 能力：默认配置 + 多 overlay 深度合并；支持 workspace_root 语义；不落盘 secrets
- 入口（契约/使用）：`help/02-config-reference.cn.md`
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/config/loader.py`

### CAP-SDK-002：Agent 基础闭环（run / run_stream）

- 能力：run_stream 事件流；run 返回 final_output/events_path（locator；兼容字段）；默认 file WAL（JSONL）落盘，终态事件 payload 同时提供 `wal_locator`（推荐字段）
- 入口（契约/使用）：`help/03-sdk-python-api.cn.md`
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/core/agent.py`

### CAP-SDK-003：Tools 协议与注册表（ToolRegistry）

- 能力：ToolSpec/ToolCall/ToolResult 契约；builtin tools 注册；dispatch 落 `tool_call_*` 事件
- 入口（契约/使用）：`help/06-tools-and-safety.cn.md`（含证据字段）
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/tools/registry.py`

### CAP-SDK-004：Safety + Approvals

- 能力：allow/ask/deny；审批超时；审计字段与 error_kind 分类
- 入口（契约/使用）：`help/06-tools-and-safety.cn.md`
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/safety/*`

### CAP-SDK-005：OS Sandbox（seatbelt/bubblewrap）

- 能力：框架层 sandbox policy gate；OS adapter auto；可观测 `data.sandbox.*`
- 入口（契约/使用）：`help/sandbox-best-practices.cn.md`
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/sandbox/*`

### CAP-SDK-006：Exec sessions（exec_command/write_stdin）

- 能力：PTY-backed exec sessions；跨进程可复用（runtime server 生命周期内）
- 入口（使用）：`docs_for_coding_agent/01-recipes.md`（配方 3）
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/runtime/server.py`

### CAP-SDK-007：Collab primitives（spawn/wait/send/close/resume）

- 能力：跨进程 child id；wait 可观测 cancelled/completed
- 入口（使用）：`docs_for_coding_agent/01-recipes.md`（配方 3/7）
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/tools/collab.py`

### CAP-SDK-008：Skills V2（preflight/scan/mentions）

- 能力：explicit spaces/sources；严格 mention；preflight 零 IO；scan 报告 jsonable
- 入口（契约/使用）：`help/05-skills-guide.cn.md`
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/skills/*`

### CAP-SDK-009：Skills sources（filesystem/in-memory/redis/pgsql）

- 能力：sources contract；redis/pgsql 可选依赖；离线回归夹具
- 入口（契约/使用）：`help/05-skills-guide.cn.md`（sources 段落）
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/skills/sources/*`

### CAP-SDK-010：State / WAL（resume replay + fork）

- 能力：JSONL WAL；resume_strategy=replay；fork_run 分叉复跑
- 入口（契约/使用）：`help/08-architecture-internals.cn.md`
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/state/*`

### CAP-SDK-011：Observability（事件/错误分类）

- 能力：error_kind 分类；events_path（locator）与 wal_locator 可定位；sandbox/approvals 可观测字段
- 入口（使用）：`help/09-troubleshooting.cn.md`
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/observability/*`

### CAP-SDK-012：Studio MVP（API + SSE）

- 能力：sessions/runs/approvals API；SSE events；信息面板（sandbox/approvals/config）
- 入口：`help/07-studio-guide.md`、`packages/skills-runtime-studio-mvp/README.md`

### CAP-SDK-013：CLI（skills/tools）

- 能力：skills preflight/scan；tools list/run；明确 exit code 语义
- 入口（契约/使用）：`help/04-cli-reference.cn.md`
- 入口（实现）：`packages/skills-runtime-sdk-python/src/agent_sdk/cli/*`

### CAP-SDK-014：Plan + Human I/O（update_plan / request_user_input）

- 能力：结构化计划同步（plan_updated）；结构化人机输入（human_request/human_response）；无 provider 必须 fail-closed
- 入口（契约/使用）：`help/04-cli-reference.cn.md`
- 入口（示例）：`examples/step_by_step/08_plan_and_user_input/`

### CAP-SDK-015：Web/Image Tools（web_search / view_image）

- 能力：`web_search` 默认关闭（需显式注入 provider）；`view_image` 受 workspace 边界约束（仅演示型）
- 入口（契约/使用）：`help/06-tools-and-safety.cn.md`
- 入口（示例）：`examples/tools/02_web_search_disabled_and_fake_provider/`、`examples/workflows/19_view_image_offline/`

---

## 备注：未来事项（Backlog）在哪里看？

OSS 版本不强制携带内部 backlog/台账文件。建议：
- 对外：以 `help/`、`examples/`、`tests/` 的可复现证据为准；
- 内部：以你们自己的 backlog/工单系统为准，并在变更中附带证据链（命令 + 输出 + 决策）。
