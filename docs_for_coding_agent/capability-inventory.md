# Capability Inventory（SDK 全能力点清单）

> 约定：CAP-* 是“能力点”，不是 backlog。  
> Backlog 的未来事项以 `docs/backlog.md` 的 BL-* 为准。

本清单目标：做到“不遗漏”，便于编码智能体快速确认：
- 当前 SDK 已具备哪些能力（可回归/可复刻）
- 每个能力点对应的契约（spec）、示例（examples）、回归（tests）、接入手册（help）

---

## CAP-SDK-*（核心能力域）

### CAP-SDK-001：配置与 overlay（Config Loader）

- 能力：默认配置 + 多 overlay 深度合并；支持 workspace_root 语义；不落盘 secrets
- 入口：`help/02-config-reference.cn.md`、`docs/specs/skills-runtime-sdk/docs/configuration.md`

### CAP-SDK-002：Agent 基础闭环（run / run_stream）

- 能力：run_stream 事件流；run 返回 final_output/events_path；WAL(JSONL) 落盘
- 入口：`help/03-sdk-python-api.cn.md`、`docs/specs/skills-runtime-sdk/docs/agent-loop.md`

### CAP-SDK-003：Tools 协议与注册表（ToolRegistry）

- 能力：ToolSpec/ToolCall/ToolResult 契约；builtin tools 注册；dispatch 落 `tool_call_*` 事件
- 入口：`docs/specs/skills-runtime-sdk/docs/tools.md`、`docs/specs/skills-runtime-sdk/docs/tools-standard-library.md`

### CAP-SDK-004：Safety + Approvals

- 能力：allow/ask/deny；审批超时；审计字段与 error_kind 分类
- 入口：`help/06-tools-and-safety.cn.md`、`docs/specs/skills-runtime-sdk/docs/safety.md`

### CAP-SDK-005：OS Sandbox（seatbelt/bubblewrap）

- 能力：框架层 sandbox policy gate；OS adapter auto；可观测 `data.sandbox.*`
- 入口：`help/sandbox-best-practices.cn.md`、`docs/specs/skills-runtime-sdk/docs/os-sandbox.md`

### CAP-SDK-006：Exec sessions（exec_command/write_stdin）

- 能力：PTY-backed exec sessions；跨进程可复用（runtime server 生命周期内）
- 入口：`docs/specs/skills-runtime-sdk/docs/tools-exec-sessions.md`

### CAP-SDK-007：Collab primitives（spawn/wait/send/close/resume）

- 能力：跨进程 child id；wait 可观测 cancelled/completed
- 入口：`docs/specs/skills-runtime-sdk/docs/tools-collab.md`

### CAP-SDK-008：Skills V2（preflight/scan/mentions）

- 能力：explicit spaces/sources；严格 mention；preflight 零 IO；scan 报告 jsonable
- 入口：`help/05-skills-guide.cn.md`、`docs/specs/skills-runtime-sdk/docs/skills.md`

### CAP-SDK-009：Skills sources（filesystem/in-memory/redis/pgsql）

- 能力：sources contract；redis/pgsql 可选依赖；离线回归夹具
- 入口：`docs/specs/skills-runtime-sdk/docs/skills-sources-contract.md`

### CAP-SDK-010：State / WAL（resume replay + fork）

- 能力：JSONL WAL；resume_strategy=replay；fork_run 分叉复跑
- 入口：`docs/specs/skills-runtime-sdk/docs/state.md`

### CAP-SDK-011：Observability（事件/错误分类）

- 能力：error_kind 分类；events_path 可定位；sandbox/approvals 可观测字段
- 入口：`docs/specs/skills-runtime-sdk/docs/observability.md`、`help/09-troubleshooting.cn.md`

### CAP-SDK-012：Studio MVP（API + SSE）

- 能力：sessions/runs/approvals API；SSE events；信息面板（sandbox/approvals/config）
- 入口：`help/07-studio-guide.md`、`packages/skills-runtime-studio-mvp/README.md`

### CAP-SDK-013：CLI（skills/tools）

- 能力：skills preflight/scan；tools list/run；明确 exit code 语义
- 入口：`help/04-cli-reference.cn.md`、`docs/specs/skills-runtime-sdk/docs/tools-cli.md`

### CAP-SDK-014：Plan + Human I/O（update_plan / request_user_input）

- 能力：结构化计划同步（plan_updated）；结构化人机输入（human_request/human_response）；无 provider 必须 fail-closed
- 入口：`docs/specs/skills-runtime-sdk/docs/tools-plan-and-input.md`、`help/04-cli-reference.cn.md`

### CAP-SDK-015：Web/Image Tools（web_search / view_image）

- 能力：`web_search` 默认关闭（需显式注入 provider）；`view_image` 受 workspace 边界约束（仅演示型）
- 入口：`docs/specs/skills-runtime-sdk/docs/tools-web-and-image.md`、`help/06-tools-and-safety.cn.md`

---

## 备注：未来事项在哪里看？

- 未来事项（TODO/out-of-scope）统一在：`docs/backlog.md`（BL-*）
- 已交付事项备忘也在：`docs/backlog.md` 的 done memo（避免“完成了却找不到证据”）
