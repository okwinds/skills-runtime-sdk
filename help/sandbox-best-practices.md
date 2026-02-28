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

## 2.1 Profile gradients (dev/balanced/prod) and rollback

For staged hardening, prefer `sandbox.profile` as a macro:

- `dev`: availability-first (does not enforce OS sandbox by default)
- `balanced`: recommended default (restricted + auto backend; Linux defaults to network isolation)
- `prod`: production-hardening baseline (tighten further via overlays)

Notes:
- `sandbox.profile` is expanded by the config loader into `sandbox.default_policy` + `sandbox.os.*`.
- `sandbox.profile` is a baseline preset: explicit `sandbox.default_policy` / `sandbox.os.*` fields override the preset (explicit > preset).
- Rollback is config-only (for example `prod -> balanced`), no code changes required.

Minimal offline regression with auditable output:

```bash
bash scripts/integration/sandbox_profile_regression.sh dev
bash scripts/integration/sandbox_profile_regression.sh balanced
bash scripts/integration/sandbox_profile_regression.sh prod
```

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

---

## 8. Docker / container notes (Debian 13, Ubuntu 20.04/24.04 examples)

In containers, “does the OS sandbox work” depends on the image, the host kernel, and container security settings (seccomp/AppArmor/capabilities).

### 8.1 macOS seatbelt (`sandbox-exec`) cannot run inside Linux containers

- Seatbelt is a macOS (Darwin) userland feature.
- Debian/Ubuntu containers are Linux userland, so they cannot provide `sandbox-exec`.
- Conclusion: **seatbelt is not available inside containers**. Use it only when running the SDK/tools directly on a macOS host.

### 8.2 Linux bubblewrap (`bwrap`) is “conditionally available” in containers

Common prerequisites (if unmet you may see `sandbox_denied` or `Operation not permitted` from bwrap):
- `bubblewrap` is installed in the container (provides `bwrap`).
- The host kernel and container policies allow user namespaces (and `unshare` is not blocked by seccomp/AppArmor).

Quick checks (run inside the container):

```bash
command -v bwrap || true
bwrap --version || true
cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || true
cat /proc/sys/user/max_user_namespaces 2>/dev/null || true
```

Notes:
- On Ubuntu, `kernel.unprivileged_userns_clone=0` disables unprivileged user namespaces (you usually can’t enable it from inside a container).
- `max_user_namespaces=0` can also break bwrap.

### 8.3 Docker probe examples (use privileged for probing, not as a production default)

This repo includes a one-shot probe script (Debian-family, requires privileged):

```bash
bash scripts/integration/os_sandbox_bubblewrap_probe_docker.sh
```

If you prefer an Ubuntu-based probe (example: Ubuntu 24.04), you can use:

```bash
docker run --rm \
  --privileged \
  --security-opt seccomp=unconfined \
  --entrypoint bash \
  ubuntu:24.04 -lc '
    set -eu
    apt-get update -qq
    apt-get install -y -qq bubblewrap >/dev/null
    bwrap --version
    mkdir -p /tmp/work
    echo hi >/tmp/work/hi.txt
    bwrap --die-with-parent --unshare-net \
      --proc /proc --dev /dev \
      --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /lib /lib --ro-bind /etc /etc \
      --bind /tmp/work /work --chdir /work -- /bin/cat /work/hi.txt
  '
```

Similarly you can probe with Debian 13 (often referred to as “trixie”) or Ubuntu 20.04 (“focal”). The key variables are:
- whether user namespaces are allowed
- whether seccomp/AppArmor blocks required syscalls
- whether `bwrap` can be installed and executed

### 8.4 macOS host + Docker Desktop nuance (common confusion)

If your host OS is macOS but you run Debian/Ubuntu containers via Docker Desktop:

- Containers run on a **Linux VM kernel** (not Darwin), so the container userland is still **Linux**.
- Conclusion #1: **seatbelt (`sandbox-exec`) is not available inside containers**. Use it only when running the SDK/tools directly on a macOS host.
- Conclusion #2: inside containers, OS sandboxing (if any) is **bubblewrap (`bwrap`)**, and it still depends on Linux VM kernel support for user namespaces + Docker security settings (seccomp/AppArmor/capabilities).
- Conclusion #3: the probe script `scripts/integration/os_sandbox_bubblewrap_probe_docker.sh` is still the right starting point (treat it as “capability probing”, not a production baseline).
