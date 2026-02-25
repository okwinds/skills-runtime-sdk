<div align="center">

[English](14-safety-deep-dive.md) | [中文](14-safety-deep-dive.cn.md) | [Help](README.md)

</div>

# 14. Safety deep dive: Gatekeeper vs Fence

This document explains how to make Safety/Sandbox **strict enough to be safe**, while still being **practical for developer UX** and **unattended cloud automation**.

If you only need the reference, start with:
- `help/06-tools-and-safety.md` (tool list + config knobs)
- `help/sandbox-best-practices.md` (platform notes + probes)

## 14.1 Two-layer model (non-negotiable)

The runtime deliberately separates safety into **two different layers**:

- **Gatekeeper (Policy + Approvals)**: decides *whether* a tool call is allowed to run.
- **Fence (OS sandbox)**: limits *what an allowed tool call can access/do*.

```text
ToolCall
  │
  ├─ Guard (risk detection)           → risk_level + reason
  │
  ├─ Policy (deterministic gate)      → allow | ask | deny
  │
  ├─ Approvals (human/programmatic)   → approved | approved_for_session | denied | abort
  │
  └─ OS Sandbox (execution isolation) → none | restricted
```

Why both:
- A gatekeeper without a fence is “allowed = full host access”.
- A fence without a gatekeeper creates usability traps (everything runs, but fails mysteriously) and makes it hard to explain “why something is blocked”.

## 14.2 Auditable approvals without leaking secrets

Approvals are designed to be **auditable and cacheable** without storing sensitive plaintext.

### Approval key

`approval_key = sha256(canonical_json(tool, sanitized_request))`

- Canonical JSON is sorted and stable to maximize cache hit rate.
- The request is **sanitized** (audit-friendly, non-leaking) before hashing and before being sent to any UI.

### What is recorded vs redacted (examples)

The framework sanitizes common high-risk tools as follows:

- `shell_exec`
  - Records: `argv`, `cwd`, `timeout_ms`, `tty`, `sandbox`, `sandbox_permissions`, `risk`
  - Records only `env_keys` (never env values)
- `file_write`
  - Records: `path`, `create_dirs`, `sandbox_permissions`
  - Stores `bytes` + `content_sha256` (never raw content)
- `apply_patch`
  - Records best-effort impacted `file_paths`
  - Stores `bytes` + `content_sha256` (never raw patch content)

Operational rule of thumb:
- **Never put secrets in argv.** If you run `curl -H "Authorization: Bearer ..."` the token becomes auditable plaintext.
- Prefer env injection (`env` / `.env`) + placeholder expansion inside the shell.

## 14.3 Which tools use Gatekeeper? Which tools use Fence?

### Fence (OS sandbox)

OS sandbox only applies to tools that actually execute commands, and only when `sandbox=restricted` is effective:
- `shell_exec`
- `shell` / `shell_command` (wrappers over `shell_exec`)
- `exec_command` (PTY-backed, also supports sandbox wrapping)

If `restricted` is required but no adapter is configured/available, the tool MUST fail with:
- `error_kind="sandbox_denied"`
- no silent fallback

### Gatekeeper (policy + approvals)

At minimum, the framework MUST prevent any “command execution path” from bypassing policy/approvals.

In unattended environments:
- If `safety.mode=ask` and a tool call requires approval but no `ApprovalProvider` is configured,
  the run MUST fail-fast (`run_failed`, `error_kind="config_error"`) to avoid “infinite retry loops”.

## 14.4 Unattended cloud automation (recommended pattern)

Goal: **do not block pipelines**, but also do not fail-open.

Recommended:
- Keep `safety.mode=ask`
- Inject a **programmatic** `ApprovalProvider` (rule-based), defaulting to **DENIED** (fail-closed)

Example skeleton (allow only `pytest`):

```python
from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.safety.rule_approvals import ApprovalRule, RuleBasedApprovalProvider
from skills_runtime.safety.approvals import ApprovalDecision

provider = RuleBasedApprovalProvider(
    rules=[
        ApprovalRule(
            tool="shell_exec",
            condition=lambda req: (req.details.get("argv") or [None])[0] == "pytest",
            decision=ApprovalDecision.APPROVED,
        )
    ],
    default=ApprovalDecision.DENIED,
)

agent = Agent(workspace_root=Path(".").resolve(), backend=..., approval_provider=provider)
```

Notes:
- Use `approved_for_session` if you want to reduce repeated prompts for identical actions.
- Keep rules narrow; treat expansions as “security review events”.

## 14.5 Developer UX: “strict enough, not annoying”

The main levers:

- `safety.allowlist`: reduce prompts for common safe commands (`rg`, `pytest`, `cat`, etc.)
- `safety.denylist`: block obviously dangerous commands early (`sudo`, `rm -rf`, `mkfs`, ...)
- approvals cache (`approved_for_session`): reduce repeated prompts for the same action
- `sandbox.default_policy` + sandbox profile gradients: keep a fence, but avoid overly strict defaults in local dev

Practical recommendation:
- Local dev: `mode=ask` + a reasonable allowlist + a minimal sandbox profile
- Production: `mode=ask` (or tighter) + stricter sandbox profile + conservative denylist

See the Studio example overlay:
- `packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example`

## 14.6 Common pitfalls (and how to avoid them)

1) **Secrets in argv**
   - Bad: `curl -H "Authorization: Bearer <token>"`
   - Better: `env={"TOKEN": "..."}; curl -H "Authorization: Bearer $TOKEN"`

2) **Assuming “requires_approval=true” is enough**
   - The gate MUST be enforced in the Agent loop, not only hinted in `ToolSpec`.
   - Parity wrappers (like `shell_command`) must not create bypasses.

3) **Sandbox expectations**
   - `sandbox=restricted` is about OS isolation for tool execution, not about LLM HTTP calls.
   - Linux `bubblewrap` can unshare network for the tool process, while the SDK itself can still call the LLM backend outside the sandbox.

## 14.7 Source pointers (where the truth lives)

- Agent gate orchestration: `packages/skills-runtime-sdk-python/src/skills_runtime/core/agent.py`
- Policy and risk detection: `packages/skills-runtime-sdk-python/src/skills_runtime/safety/policy.py`, `.../guard.py`
- Approvals protocol: `packages/skills-runtime-sdk-python/src/skills_runtime/safety/approvals.py`
- OS sandbox adapters: `packages/skills-runtime-sdk-python/src/skills_runtime/sandbox.py`
- Builtin exec tools: `packages/skills-runtime-sdk-python/src/skills_runtime/tools/builtin/`

