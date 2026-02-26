<div align="center">

[English](14-safety-deep-dive.md) | [中文](14-safety-deep-dive.cn.md) | [Help](README.md)

</div>

# 14. Safety deep dive: Gatekeeper vs Fence

This document explains how to make Safety/Sandbox **strict enough to be safe**, while still being **practical for developer UX** and **unattended cloud automation**.

If you only need the reference, start with:
- `help/06-tools-and-safety.md` (tool list + config knobs)
- `help/sandbox-best-practices.md` (platform notes + probes)

If you are implementing or auditing the safety posture, this doc focuses on:
- *Where the runtime makes decisions* (Guard → Policy → Approvals → Sandbox)
- *What is recorded vs redacted* (auditable but non-leaking)
- *How wrappers stay in parity* (no bypass through `shell_command`/`exec_command`)
- *How exec sessions stay safe* (PTY + `write_stdin`)

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

### 14.1.1 Mental model: “decide” then “constrain”

```text
Gatekeeper answers: “May we run this?”
Fence answers:     “Even if we run it, what can it touch?”

Allowed without fence   → full host access
Fence without gatekeeper→ confusing failures + hard-to-explain UX
Both together           → explainable + auditable + constrained
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

### 14.2.1 What does “sanitized request” mean?

Sanitization is a **data contract**:
- capture enough to answer “what happened / what did we approve?”
- do *not* capture plaintext secrets or large payloads

```text
Raw tool args (may contain secrets / large data)
   │
   ├─ sanitize → small, stable, audit-friendly shape
   │
   ├─ hash     → approval_key (cache + loop guard)
   │
   └─ persist  → WAL events + approvals UI (no secrets)
```

### What is recorded vs redacted (examples)

The framework sanitizes common high-risk tools as follows:

- `shell_exec`
  - Records: `argv`, `cwd`, `timeout_ms`, `tty`, `sandbox`, `sandbox_permissions`, `risk`
  - Records only `env_keys` (never env values)
- `shell` (argv form)
  - Records: `argv` (from `command` list), `cwd`, `timeout_ms`, `tty`, `sandbox*`, `risk`
  - Records only `env_keys` (never env values)
- `shell_command` / `exec_command` (shell string wrappers)
  - Records: original `command/cmd` string
  - Records: `intent.argv` best-effort parsed from the shell string (for policy + auditing)
  - Records: `intent.is_complex` and `intent.reason` (why we treated it as complex)
  - Records only `env_keys` (never env values)
- `write_stdin`
  - Records: `session_id`, `bytes`, `chars_sha256`, `is_poll`
  - Never records plaintext `chars`
- `file_write`
  - Records: `path`, `create_dirs`, `sandbox_permissions`
  - Stores `bytes` + `content_sha256` (never raw content)
- `apply_patch`
  - Records best-effort impacted `file_paths`
  - Stores `bytes` + `content_sha256` (never raw patch content)

### 14.2.2 Why “hash fingerprints” instead of plaintext?

This gives you:
- auditability (“we approved writing *this exact content*”)
- debuggability (“the content changed” → different hash → new approval)
- non-leakage (no secrets or big diffs in WAL/UI)

Minimal pattern:
```text
approval payload: bytes + sha256
NOT: raw patch / raw file content / raw stdin
```

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

### 14.3.1 “Command execution path” inventory (common bypass sources)

These tool names *look different* but must share one safety posture:

```text
shell_exec(argv=[...])          ┐
shell(command=[...])            │ same policy + approvals semantics
shell_command(command="...")    │ (wrappers must not bypass)
exec_command(cmd="...")         ┘

skill_exec(skill_mention, action_id)
  └─ resolves to an underlying shell action (must be gated like shell_exec)
```

If you audit safety, always verify parity across the wrappers.

## 14.4 Policy decision tree (denylist/allowlist/mode/risk)

Policy is deterministic: same sanitized request + same config must produce the same decision.

For `shell_exec`-like paths, the high-level decision tree is:

```text
                +-------------------------------+
                | denylist hit?                 |
                +-------------------------------+
                      | yes → DENY (no approvals)
                      v no
                +-------------------------------+
                | safety.mode == deny ?         |
                +-------------------------------+
                      | yes → DENY (no approvals)
                      v no
                +-------------------------------+
                | sandbox_permissions escalated?|
                +-------------------------------+
                      | yes → ASK (must approve)
                      v no
                +-------------------------------+
                | allowlist hit?                |
                +-------------------------------+
                      | yes → ALLOW (no approvals)
                      v no
                +-------------------------------+
                | safety.mode == allow ?        |
                +-------------------------------+
                      | yes → ALLOW (no approvals)
                      v no
                +-------------------------------+
                | otherwise                     |
                +-------------------------------+
                      → ASK (approvals required)
```

Notes:
- In `mode=ask`, allowlist is the primary way to reduce prompts for “known safe” commands.
- In `mode=deny`, command execution tools are denied by design (framework-level conservative default).

## 14.5 Shell wrappers: why parsing exists, and why “complex shell” is special

`shell_command` / `exec_command` accept a *shell string* (e.g. `"echo hi"`), but internally execution may be via `/bin/sh -lc <command>`.

If we naively treat the execution argv as:
```text
["/bin/sh", "-lc", "<command>"]
```
then allowlist/denylist would match `/bin/sh` instead of the intent (`echo`, `pytest`, `rg`…), making policy useless.

### 14.5.1 Intent parsing (best-effort)

The runtime therefore derives an **intent argv** for policy + auditing:

```text
command: "pytest -q"
intent.argv ≈ ["pytest", "-q"]
```

Important constraints:
- intent parsing is **not** execution (it must never change what actually runs)
- if parsing fails, we conservatively treat the command as complex/high-risk

### 14.5.2 Complex shell strings force approvals (in mode=ask)

Some strings are too “shell-ish” to be safely reasoned about with prefix allowlists:

```text
pytest && rm -rf /
rg foo src | head -n 10
echo x > ~/.ssh/config
$(curl ...)
`cat secret.txt`
```

In `safety.mode=ask`, these patterns should trigger approvals even if allowlist would otherwise match,
because the string can combine multiple actions or redirect output.

Recommended rule of thumb:
- allowlist is for “single-command intent”
- approvals are for “shell programs”

## 14.6 Exec sessions: PTY + write_stdin safely

`exec_command` may start a long-running process (PTY-backed), returning a `session_id`.
Subsequent interaction happens via `write_stdin(session_id=..., chars=...)`.

### 14.6.1 Sequence diagram (typical)

```text
LLM
  │ tool_call: exec_command(cmd="python -i", tty=true)
  v
Runtime Gatekeeper
  │ (policy/approvals)
  v
Tool executes → returns {session_id, running=true}
  │
  │ tool_call: write_stdin(session_id, chars="print(1)\\n")
  v
Runtime Gatekeeper
  │ (policy/approvals; chars are never stored in plaintext)
  v
Tool writes stdin → returns stdout/stderr deltas
```

### 14.6.2 Approval UX: avoid noisy repeats, but never fail-open

Recommended behavior:
- In `mode=ask`, `write_stdin` should be gated (interactive sessions can do anything).
- If a `session_id` has already been approved/started in the current run, further `write_stdin` calls for that session can skip repeated approvals.
- Approval requests for `write_stdin` must never store plaintext `chars`; store `bytes` + `sha256` only.

## 14.7 Unattended cloud automation (recommended pattern)

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

## 14.8 Developer UX: “strict enough, not annoying”

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

### 14.7.1 Minimal config sketch (YAML overlay)

```yaml
safety:
  mode: ask
  allowlist:
    - "pytest"
    - "rg"
    - "cat"
  denylist:
    - "sudo"
    - "rm -rf"
```

This is intentionally narrow and portable; expand only with explicit review.

## 14.9 Common pitfalls (and how to avoid them)

1) **Secrets in argv**
   - Bad: `curl -H "Authorization: Bearer <token>"`
   - Better: `env={"TOKEN": "..."}; curl -H "Authorization: Bearer $TOKEN"`

2) **Assuming “requires_approval=true” is enough**
   - The gate MUST be enforced in the Agent loop, not only hinted in `ToolSpec`.
   - Parity wrappers (like `shell_command`) must not create bypasses.

3) **Sandbox expectations**
   - `sandbox=restricted` is about OS isolation for tool execution, not about LLM HTTP calls.
   - Linux `bubblewrap` can unshare network for the tool process, while the SDK itself can still call the LLM backend outside the sandbox.

4) **Relying on allowlist for complex shell programs**
   - `allowlist: ["pytest"]` does not make `pytest && rm -rf /` safe.
   - Treat “complex shell” as approvals-required (and ideally denied by rule in unattended pipelines).

## 14.10 Source pointers (where the truth lives)

- Agent gate orchestration: `packages/skills-runtime-sdk-python/src/skills_runtime/core/agent.py`
- Policy and risk detection: `packages/skills-runtime-sdk-python/src/skills_runtime/safety/policy.py`, `.../guard.py`
- Approvals protocol: `packages/skills-runtime-sdk-python/src/skills_runtime/safety/approvals.py`
- OS sandbox adapters: `packages/skills-runtime-sdk-python/src/skills_runtime/sandbox.py`
- Builtin exec tools: `packages/skills-runtime-sdk-python/src/skills_runtime/tools/builtin/`
