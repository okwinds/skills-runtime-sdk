<div align="center">

[English](08-architecture-internals.md) | [中文](08-architecture-internals.cn.md) | [Help](README.md)

</div>

# 08. Internals: How the runtime collaborates

## 8.1 Bootstrap

Core steps:

1. Determine `workspace_root`
2. Load `.env` (if present)
3. Discover overlays (`config/runtime.yaml` + env-provided paths)
4. Deep-merge configs and validate with Pydantic
5. Build a “config sources map” (where each effective field comes from)

Why this matters:

- You can answer “where did this field come from?”
- It reduces silent failures caused by overlay drift

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

## 8.3 Event log (WAL)

Run events are appended under:

```text
<workspace_root>/.skills_runtime_sdk/runs/<run_id>/events.jsonl
```

Properties:

- append-only
- can be forwarded to SSE
- can be replayed for debugging

## 8.4 Prompt assembly

`PromptManager` is responsible for:

- choosing system/developer templates
- injecting skills (toggleable)
- trimming history with sliding windows (messages/chars)

## 8.5 Skills mechanism: key points

- Discovery: metadata-only
- Injection: load bodies based on mentions + budget limits
- Mentions: tolerant extraction in free text + strict validation in tool args

## 8.6 Tool orchestration: key points

- Registry: maintains ToolSpec + handlers
- Gate: approvals + policy
- Sandbox: wrap execution when `restricted`
- Result: normalize ToolResult and feed it back to the agent loop

## 8.7 Safety vs Sandbox layering

- Safety answers “should we allow this?”
- Sandbox answers “even if allowed, how far can it go?”

You need both; neither fully replaces the other.

## 8.8 Studio ↔ SDK integration points

- Studio backend builds an `Agent` via `_build_agent()`
- `ApprovalHub` passes frontend decisions back to the SDK’s approval waiting point
- SSE is streamed from `events.jsonl`

## 8.9 Common next steps (not shipped in the MVP)

- Multi-agent: cross-process / cross-machine state persistence and restore
- Streaming tool args: delta aggregation and “final args” state machine
- fork / resume: reconstruct from `events.jsonl` and resume from checkpoints
- Sandbox: staged tightening + improved observability (better failure attribution)

---

Prev: [`07-studio-guide.md`](./07-studio-guide.md)  
Next: [`09-troubleshooting.md`](./09-troubleshooting.md)
