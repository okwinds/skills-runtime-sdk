<div align="center">

[English](00-overview.md) | [中文](00-overview.cn.md) | [Help](README.md)

</div>

# 00. Overview: What system are you using?

## 0.1 One-sentence definition

`skills-runtime-sdk` is a **skills-first Skills Runtime framework**:

- One unified configuration for models, tools, and safety policies
- A standard event stream (WAL) for reproducibility and debugging
- A Skills mechanism to inject reusable capabilities into runs
- A Studio MVP to provide a visual entry point for sessions and runs

Note on naming:
- You still use the `Agent` API (`Agent.run()` / `Agent.run_stream()`), but conceptually **Skills are the primary extension surface**.
- `Agent` is the runtime engine that executes a run (prompt compilation → LLM → tool orchestration → WAL).

## 0.2 Repository layout (key parts only)

```text
<repo_root>/
├── packages/
│   ├── skills-runtime-sdk-python/        # Python SDK reference implementation
│   └── skills-runtime-studio-mvp/        # Studio MVP (FastAPI + React)
├── help/                                 # This handbook (CN/EN)
├── examples/                             # Reusable examples (skills, etc.)
└── scripts/                              # Regression + integration demo scripts
```

## 0.3 The four-layer model (memorize this)

1. **Config Layer**
   - Sources: embedded defaults + YAML overlays + env + session settings
   - Output: effective run config (models, timeouts, safety, sandbox, skills)

2. **Runtime Layer**
   - Entry: `Agent.run()` / `Agent.run_stream()`
   - Core: prompt compilation, Skills injection, LLM requests, tool orchestration, event logging

3. **Safety Layer**
   - Gatekeeping: denylist / allowlist / approvals
   - Isolation: OS sandbox (seatbelt / bubblewrap)

4. **Product Layer**
   - Example: Studio MVP (sessions, skills roots, runs, SSE, approvals API)

## 0.4 Terminology quick reference

- **Skill mention**: valid format is `$[account:domain].skill_name`
- **Free-text extraction**: only extracts valid mentions; invalid fragments become plain text
- **Strict tool validation**: if a tool arg requires `skill_mention`, it must be a full token
- **Agent**: the run engine instance (executes a run and emits WAL events)
- **Approval (gatekeeper)**: decides whether an action is allowed
- **Sandbox (fence)**: limits what an allowed action can do
- **WAL (`events.jsonl`)**: the audit/event log; your first stop for debugging

## 0.5 Relationship between SDK and Studio

- The SDK is the runtime core that you can call from Python projects.
- Studio MVP is a minimal “product shell” for fast validation and demos:
  - Backend: reuses the SDK + exposes REST/SSE
  - Frontend: sessions, skills, runs, approvals UI

## 0.6 What this framework is (and isn’t) good for

Good fit:
- You need reusable skills + tool calls + safety gatekeeping + event audit in a runtime
- You want both CLI/script entry points and a web UI entry point

Not a good fit:
- One-off prompts without runtime governance needs
- Frontend-only projects that don’t care about the runtime layer

## 0.7 Next

Continue with `help/01-quickstart.md` to run the minimal end-to-end flow.

---

Prev: [Help Index](README.md) · Next: [01. Quickstart](01-quickstart.md)
