<div align="center">

[English](sandbox-best-practices.md) | [中文](sandbox-best-practices.cn.md) | [Help](README.md)

</div>

# Sandbox Best Practices (SDK + Studio)

> Goal: avoid disrupting normal workflows while blocking abnormal/high-risk operations.

## 1. Mental model

- **Approval is a gatekeeper**: decides whether an action is allowed.
- **Sandbox is a fence**: even allowed actions run inside controlled boundaries.

Both layers are required and should not replace each other.

## 2. Recommended default (balanced mode)

Use this for most integrations:

- `safety.mode: ask`
- `safety.allowlist`: pass common low-risk commands
- `safety.denylist`: block dangerous commands early
- `sandbox.default_policy: restricted`
- `sandbox.os.mode: auto`

## 3. Studio MVP baseline (macOS dev + Linux prod)

Current config location: `packages/skills-runtime-studio-mvp/backend/config/runtime.yaml`

### 3.1 macOS (active now)

```yaml
sandbox:
  default_policy: "restricted"
  os:
    mode: "auto"
    seatbelt:
      # dev_min: minimal, low-interruption default
      profile: |
        (version 1)
        (allow default)

      # balanced template (more production-leaning; validate locally first):
      # profile: |
      #   (version 1)
      #   (allow default)
      #   ; minimal deny to make restrictions visible
      #   (deny file-read* (subpath "/etc"))
```

Check:

```bash
command -v sandbox-exec
```

### 3.2 Linux (production template, commented)

```yaml
# sandbox:
#   default_policy: "restricted"
#   os:
#     mode: "auto"
#     bubblewrap:
#       bwrap_path: "bwrap"
#       unshare_net: true
```

Check:

```bash
command -v bwrap
```

## 4. Integration guidance for frontend/business apps

### Scenario A: Dev-first (low interruption)

- Keep `mode=ask`
- Expand allowlist for common dev-safe commands
- Keep denylist for obvious destructive operations
- Keep `restricted`, but do not over-tighten profile too early

### Scenario B: Production-first (stability + auditability)

- Keep `mode=ask`
- Narrow allowlist to truly frequent commands
- Keep conservative denylist
- Prefer `bubblewrap.unshare_net=true` on Linux

### Scenario C: False-positive troubleshooting

Look at rejection types first:

1. `sandbox_denied`: adapter unavailable (`sandbox-exec`/`bwrap`) or sandbox requirements not met.
2. `approval_denied`: explicit deny or timeout.
3. `permission`: usually workspace path boundary violation.

## 5. FAQ

### Q1: Why does it already feel sandboxed?

Because approval gating and workspace path boundaries already constrain behavior, even before strict OS sandbox effects are considered.

### Q2: Can `restricted` break normal commands?

Yes, if runtime dependencies are missing (e.g. no `bwrap`) or policies are too strict. Start with balanced mode, then tighten gradually.

### Q3: Can we temporarily relax it?

Yes. Emergency fallback is `sandbox.default_policy=none`, but keep `safety.mode=ask` + denylist enabled.

## 6. Minimal pre-release checklist

- [ ] `sandbox-exec` (mac) / `bwrap` (linux) exists
- [ ] allowlist covers high-frequency safe commands
- [ ] denylist includes destructive commands
- [ ] `sandbox_denied` and `approval_denied` are observable
- [ ] rollback path is documented (`restricted -> none`)

## 7. How to verify “real sandbox” vs “approval-only”

Use events as evidence (not just UI feeling):

1. **Approval events**: `approval_requested` / `approval_decided` means policy gate was hit.
2. **Sandbox denial**: `tool_call_finished.result.error_kind == sandbox_denied` means sandbox layer blocked execution.
3. **Sandbox active (not necessarily strict)**: inspect `tool_call_finished.result.data.sandbox`:
   - `effective`: actual policy (`none|restricted`)
   - `active`: whether sandbox adapter was actually used
   - `adapter`: adapter type (e.g. `SeatbeltSandboxAdapter`)

Note:
- Current macOS example profile `(allow default)` is intentionally permissive.
- So even with `active=true`, output can still look host-like (e.g., absolute paths).

Extra tip:
- To prove restrictions are actually enforced (not just approvals), run:
  `bash scripts/integration/os_sandbox_restriction_demo.sh`
- To validate strict isolation, test with a tighter profile and a known-denied operation.

Recommended “visible restriction” checks (no external network required):

1) One-shot demo script (macOS/Linux):

```bash
bash scripts/integration/os_sandbox_restriction_demo.sh
```

2) Offline regression test (skips if adapter is unavailable):

```bash
pytest -q packages/skills-runtime-sdk-python/tests/test_os_sandbox_restriction_effects.py
```
