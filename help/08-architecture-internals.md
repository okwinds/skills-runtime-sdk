<div align="center">

[English](08-architecture-internals.md) | [中文](08-architecture-internals.cn.md) | [Help](README.md)

</div>

# 08. Internals: How the runtime collaborates

This chapter is a “mechanics” view of the SDK: component boundaries, who-calls-whom, and where the state lands on disk.

If you want the mental model for **tools/safety/sandbox**, read:
- `help/06-tools-and-safety.md`
- `help/14-safety-deep-dive.md`

If you want field-by-field configuration, read:
- `help/02-config-reference.md`

## 8.0 Architecture at a glance

The SDK is intentionally split into three layers:

1) **Bootstrap layer** (optional): discover overlays + load `.env` + produce “where did this config come from?”
2) **Core agent/runtime layer**: deterministic agent loop + event stream + tool orchestration
3) **Workspace runtime server** (optional): a tiny local JSON-RPC server for long-lived exec sessions / child agents

```text
                        (optional)
┌──────────────────────────────────────────────────────────────────────┐
│ Bootstrap layer                                                      │
│  - load_dotenv_if_present()                                          │
│  - discover_overlay_paths()                                          │
│  - resolve_effective_run_config()  -> sources map                    │
│  code: packages/.../skills_runtime/bootstrap.py                       │
└──────────────────────────────────────────────────────────────────────┘
                     │ (effective config, paths, env)
                     v
┌──────────────────────────────────────────────────────────────────────┐
│ Core (in-process)                                                    │
│  Agent (thin facade, public API)                                     │
│   └─ AgentLoop (turn loop, tool orchestration)                       │
│       - PromptManager (templates + skills + history trim)            │
│       - SafetyGate (unified policy/approval gate)                    │
│           └─ ToolSafetyDescriptor (per-tool safety semantics)        │
│       - ToolDispatcher/ToolRegistry (validation -> gate -> exec)     │
│       - WAL (events.jsonl) + WalEmitter (append -> hooks -> stream)  │
│  code: packages/.../skills_runtime/core/agent.py          (facade)   │
│        packages/.../skills_runtime/core/agent_loop.py     (loop)     │
│        packages/.../skills_runtime/safety/gate.py         (gate)     │
│        packages/.../skills_runtime/safety/descriptors.py  (builtin)  │
└──────────────────────────────────────────────────────────────────────┘
                     │ (optional)
                     v
┌──────────────────────────────────────────────────────────────────────┐
│ Workspace runtime server (out-of-process, per-workspace singleton)    │
│  - exec sessions: PTY-backed long-lived subprocesses                  │
│  - child agents: minimal concurrency primitive                         │
│  code: packages/.../skills_runtime/runtime/{client.py,server.py}      │
└──────────────────────────────────────────────────────────────────────┘
```

Where artifacts land (filesystem WAL backend):

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

## 8.1 Bootstrap (config discovery + sources map)

Core steps:

1. Determine `workspace_root`
2. (Optional) Load `.env` (if present)
3. Discover overlay YAMLs (stable order)
4. Deep-merge YAML dicts and validate with Pydantic (fail-fast on unknown keys)
5. Build a “config sources map” (where each effective field comes from)

Why this matters:

- You can answer “where did this field come from?”
- It reduces silent failures caused by overlay drift

### 8.1.1 Why bootstrap is a separate module

The **core `Agent` constructor is intentionally “no implicit I/O”**:
- it does not auto-load `.env`
- it does not auto-discover overlay files

That is a reproducibility guardrail: creating an `Agent(...)` should be explainable from explicit inputs
(`workspace_root`, `config_paths`, injected `env_vars`, etc.).

Bootstrap is a convenience layer used by CLI / apps:
- code: `packages/skills-runtime-sdk-python/src/skills_runtime/bootstrap.py`

### 8.1.2 Overlay discovery order

Overlay discovery is fixed and order-stable:

1) `<workspace_root>/config/runtime.yaml` (if exists)
2) `SKILLS_RUNTIME_SDK_CONFIG_PATHS` (comma/semicolon-separated)

This is implemented by:
- `discover_overlay_paths(workspace_root=...)`

### 8.1.3 Merge semantics (what “deep merge” means here)

Merge rules are deliberately simple and easy to reason about (see `_deep_merge` in
`packages/skills-runtime-sdk-python/src/skills_runtime/config/loader.py`):

- `dict + dict` → recursively merge keys
- `list` → replaced as a whole (no concatenation/dedup)
- otherwise → overlay overwrites base

```text
base:
  tools:
    allowlist: ["git", "rg"]
overlay:
  tools:
    allowlist: ["git"]     # replaces the whole list
effective:
  tools:
    allowlist: ["git"]
```

### 8.1.4 Sources map (where each field came from)

Bootstrap can return a compact “sources map” for key fields (session > env > yaml):

```text
models.executor -> env:SKILLS_RUNTIME_SDK_EXECUTOR_MODEL
llm.base_url    -> yaml:overlay:/.../config/runtime.yaml#llm.base_url
```

This is produced by:
- `resolve_effective_run_config(workspace_root=..., session_settings=...)`

## 8.2 Agent loop (simplified)

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

### 8.2.1 The real loop: key responsibilities and invariants

The agent loop does more than “call model then run tools”; it also:
- enforces step/wall-time budgets (`LoopController`)
- writes **every** important transition to WAL (debuggability + replayability)
- handles context-recovery modes (e.g., compaction turns) deterministically
- keeps approvals ordering stable relative to tool events (so UIs don’t drift)

Key modules:
- `packages/skills-runtime-sdk-python/src/skills_runtime/core/agent.py` (thin facade, ~296 lines)
- `packages/skills-runtime-sdk-python/src/skills_runtime/core/agent_loop.py` (AgentLoop: turn loop + tool orchestration)
- `packages/skills-runtime-sdk-python/src/skills_runtime/core/loop_controller.py`
- `packages/skills-runtime-sdk-python/src/skills_runtime/safety/gate.py` (SafetyGate: unified policy/approval gate)
- `packages/skills-runtime-sdk-python/src/skills_runtime/safety/descriptors.py` (7 built-in ToolSafetyDescriptors)

### 8.2.2 Sequence diagram (LLM + tools + approvals + WAL)

```text
Caller/UI            Agent                 LLM backend        ToolRegistry/Handlers
   |                  |                       |                   |
   | run_stream()     |                       |                   |
   |----------------->|                       |                   |
   |                  | run_started (WAL)     |                   |
   |                  |---------------------->| (emit)            |
   |                  | build prompt          |                   |
   |                  | call stream_chat()    |                   |
   |                  |---------------------->|                   |
   |                  | llm_response_delta    |                   |
   |  events (yield)  |<----------------------|                   |
   |<-----------------|                       |                   |
   |                  | tool_call_requested   |                   |
   |                  | approval gate?        |                   |
   |                  |  - policy eval        |                   |
   |                  |  - ApprovalProvider   |                   |
   |                  | tool_call_started     |                   |
   |                  | dispatch(call)        |                   |
   |                  |------------------------------------------>|
   |                  | tool_call_finished    |                   |
   |                  | inject tool msg       |                   |
   |                  | loop / finish         |                   |
   |                  | run_completed (WAL)   |                   |
   |<-----------------|                       |                   |
```

Notes:
- Tool arguments are sanitized before writing to WAL (e.g., env keys only; file_write content → sha256).
- Some tool events may be emitted via `WalEmitter.append(...)` to preserve a strict sequence.

## 8.3 Event log (WAL)

Run events are appended under:

```text
<workspace_root>/.skills_runtime_sdk/runs/<run_id>/events.jsonl
```

Properties:

- append-only
- can be forwarded to SSE
- can be replayed for debugging

### 8.3.1 What is an “event”?

An event is an `AgentEvent` object serialized as one JSON line. Typical types:
- `run_started`, `prompt_compiled`
- `llm_response_delta` / `llm_response_completed`
- `tool_call_requested`, `tool_call_started`, `tool_call_finished`
- terminal: `run_completed`, `run_failed`, `run_cancelled`

Why JSONL:
- streaming-friendly (append a line; consumers can tail)
- resilient to partial writes (worst-case only the last line is corrupt)
- cheap to inspect (`rg`, `jq -c`, etc.)

### 8.3.2 `wal_locator` and line indexes

For the filesystem WAL backend, `wal_locator` is the resolved file path string to the WAL file.

Internally:
- `JsonlWal.append(...)` returns a **0-based line index**
- the index is used as the stable “position” for replay/fork in later phases

Code:
- `packages/skills-runtime-sdk-python/src/skills_runtime/state/jsonl_wal.py`

### 8.3.3 Unified event pipeline (`WalEmitter`)

To keep event ordering consistent, core code emits via a single pipeline:

```text
WalEmitter.emit(event)
  -> wal.append(event)      # durable
  -> hooks(event)           # observability (fail-open)
  -> stream(event)          # yield to caller (UI / CLI)
```

Code:
- `packages/skills-runtime-sdk-python/src/skills_runtime/state/wal_emitter.py`

## 8.4 Prompt assembly

`PromptManager` is responsible for:

- choosing system/developer templates
- injecting skills (toggleable)
- trimming history with sliding windows (messages/chars)

### 8.4.1 Fixed injection order (why it matters)

Prompt injection order is fixed so you can reason about prompt drift:

1) system template
2) developer policy (merged into system for chat.completions compatibility)
3) skills list section (optional)
4) injected skill bodies (the “skill text” you asked for)
5) conversation history (trimmed)
6) current user task / user input

```text
┌─────────────┐
│ system      │  <- includes “[Developer Policy] ...”
└─────────────┘
┌─────────────┐
│ user        │  <- optional “Available skills ...”
└─────────────┘
┌─────────────┐
│ user        │  <- injected skill A
├─────────────┤
│ user        │  <- injected skill B
└─────────────┘
┌─────────────┐
│ history...  │  <- trimmed sliding window
└─────────────┘
┌─────────────┐
│ user        │  <- current task
└─────────────┘
```

Code:
- `packages/skills-runtime-sdk-python/src/skills_runtime/prompts/manager.py`

## 8.5 Skills mechanism: key points

- Discovery: metadata-only
- Injection: load bodies based on mentions + budget limits
- Mentions: tolerant extraction in free text + strict validation in tool args

### 8.5.1 Concepts: spaces, sources, and namespaces

Skills are grouped by:
- **namespace**: a validated slug chain like `team:product:agent`
- **space**: a configuration unit that binds a namespace to one or more sources
- **source**: where skill files/records come from (filesystem / in-memory / redis / pgsql)

```text
skills/
  spaces[]:  (namespace + enabled + sources[])
  sources[]: (id + type + connection/config)
```

Code:
- `packages/skills-runtime-sdk-python/src/skills_runtime/skills/manager.py`
- `packages/skills-runtime-sdk-python/src/skills_runtime/skills/mentions.py`

### 8.5.2 Scan vs inject (metadata-only vs body load)

The runtime tries to keep the “cold path” cheap:

```text
scan():   read frontmatter/metadata -> build index (no body)
inject(): load SKILL.md body only for mentioned skills (budgeted)
```

This is why you can list available skills quickly, while still controlling how much text is injected into the model.

### 8.5.3 Refresh policy and caching

Scanning can be configured with:
- `always` (force rescan)
- `ttl` (rescan after `ttl_sec`)
- `manual` (only scan when asked)

This makes “interactive use” fast while keeping “CI/validation” deterministic.

## 8.6 Tool orchestration: key points

- Registry: maintains ToolSpec + handlers
- Gate: approvals + policy
- Sandbox: wrap execution when `restricted`
- Result: normalize ToolResult and feed it back to the agent loop

### 8.6.1 Two planes: protocol vs execution

There are two related pieces:

1) **Tool protocol**: a `ToolSpec` is what the model sees (name + JSON schema)
2) **Tool execution**: a handler function runs with a `ToolExecutionContext`

```text
ToolSpec  ->  LLM tool_calls  ->  ToolCall(args)  -> validate -> SafetyGate(ToolSafetyDescriptor) -> handler -> ToolResult
```

Code:
- `packages/skills-runtime-sdk-python/src/skills_runtime/tools/protocol.py` (ToolSpec, ToolSafetyDescriptor, PassthroughDescriptor)
- `packages/skills-runtime-sdk-python/src/skills_runtime/tools/registry.py`
- `packages/skills-runtime-sdk-python/src/skills_runtime/safety/gate.py` (SafetyGate)
- `packages/skills-runtime-sdk-python/src/skills_runtime/safety/descriptors.py` (built-in descriptors)

### 8.6.2 The execution context is part of the security boundary

`ToolExecutionContext` enforces several “framework guardrails”:
- `resolve_path(...)` forbids escaping `workspace_root`
- env is merged as `ctx.env` (session store) + per-call overrides (keys are observable; values are not logged)
- event emission sanitizes sensitive fields before writing to WAL

Even if a tool handler has a bug, these constraints reduce blast radius.

## 8.7 Safety vs Sandbox layering

- Safety answers “should we allow this?”
- Sandbox answers “even if allowed, how far can it go?”

You need both; neither fully replaces the other.

```text
        decision plane                    execution plane
┌──────────────────────────┐     ┌──────────────────────────────────────┐
│ Safety / Policy / Approvals│    │ OS sandbox (seatbelt/bwrap/none)     │
│  - allow/ask/deny          │    │  - filesystem/network constraints     │
│  - allowlist/denylist      │    │  - process isolation                  │
└──────────────────────────┘     └──────────────────────────────────────┘
```

## 8.8 Studio ↔ SDK integration points

- Studio backend builds an `Agent` via `_build_agent()`
- `ApprovalHub` passes frontend decisions back to the SDK’s approval waiting point
- SSE is streamed from `events.jsonl`

## 8.9 Common next steps (not shipped in the MVP)

- Multi-agent: cross-process / cross-machine state persistence and restore
- Streaming tool args: delta aggregation and “final args” state machine
- fork / resume: reconstruct from `events.jsonl` and resume from checkpoints
- Sandbox: staged tightening + improved observability (better failure attribution)

## 8.10 Workspace runtime server (exec sessions / child agents)

Purpose:
- Host cross-process reusable exec sessions (PTY + child processes)
- Provide a minimal collab child-agent primitive (early multi-agent/concurrency building blocks)

Workspace paths:

```text
<workspace_root>/.skills_runtime_sdk/runtime/
  - runtime.sock         # Unix socket (JSON RPC)
  - server.json          # pid/secret/socket_path/created_at_ms
  - server.stdout.log    # server stdout (for debugging)
  - server.stderr.log    # server stderr (for debugging)
  - exec_registry.json   # crash/restart orphan cleanup registry (pids + marker)
```

Observable RPCs:
- `runtime.status`: server health + counts (active exec sessions / active children) + registry summary
- `runtime.cleanup`: explicit stop/cleanup (close exec sessions + cancel children)

### 8.10.1 Why a separate process?

Some capabilities are awkward to implement purely in-process:
- a long-lived PTY session should survive a single tool call
- concurrent “child agents” should not block the main loop

So the SDK uses a per-workspace singleton server:
- start-on-demand (`RuntimeClient.ensure_server()`)
- authenticate with a local secret stored in `server.json`
- auto-exit on idle to avoid leaving stray background processes (important for tests/CI)

Code:
- `packages/skills-runtime-sdk-python/src/skills_runtime/runtime/client.py`
- `packages/skills-runtime-sdk-python/src/skills_runtime/runtime/server.py`

### 8.10.2 JSON-RPC shape (simplified)

This is not a public network API; it is a local Unix socket protocol.

```json
{"method":"runtime.status","params":{},"secret":"<from server.json>"}
```

Offline debugging example:

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

Prev: [`07-studio-guide.md`](./07-studio-guide.md)  
Next: [`09-troubleshooting.md`](./09-troubleshooting.md)
