<div align="center">

[English](09-troubleshooting.md) | [中文](09-troubleshooting.cn.md) | [Help](README.md)

</div>

# 09. Troubleshooting Guide (symptom → diagnosis → fix)

## 9.1 Fast triage order

1. Read `events.jsonl` first
2. Check API error `error_kind`
3. Verify workspace / overlays / env
4. Verify approvals and sandbox dependencies

## 9.2 Common failures matrix

### A) Python import fails (`str | None` typing error)

Symptom:
- Running CLI/SDK on Python 3.9 fails with typing errors

Diagnosis:

```bash
python3 -V
```

Fix:
- Use Python `>=3.10`

---

### B) `overlay config not found`

Symptom:
- The run fails immediately, saying an overlay path does not exist

Diagnosis:

```bash
echo "$SKILLS_RUNTIME_SDK_CONFIG_PATHS"
```

Fix:
- Remove invalid paths or replace with existing ones
- In Studio MVP, prefer `packages/skills-runtime-studio-mvp/backend/config/runtime.yaml`

---

### C) `env file not found`

Symptom:
- Startup fails with `env file not found`

Diagnosis:

```bash
echo "$SKILLS_RUNTIME_SDK_ENV_FILE"
```

Fix:

```bash
unset SKILLS_RUNTIME_SDK_ENV_FILE
```

---

### D) `sandbox_denied`

Symptom:
- A tool execution fails with `error_kind=sandbox_denied`

Diagnosis:

```bash
command -v sandbox-exec || true
command -v bwrap || true
```

Container/Docker extra checks (Linux):
- If you enable `bubblewrap` (`bwrap`) inside a Debian/Ubuntu container but it still fails, user namespaces or container policies are common causes:

```bash
cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || true
cat /proc/sys/user/max_user_namespaces 2>/dev/null || true
```

- One-shot probe (requires privileged; use for probing only, not as a production default):

```bash
bash scripts/integration/os_sandbox_bubblewrap_probe_docker.sh
```

Fix:
- Install the missing OS sandbox adapter
- Or temporarily set `sandbox.default_policy` to `none` (keep approvals + denylist)

Tip: to prove “real OS sandbox restriction effects” (not just approvals), run:

```bash
bash scripts/integration/os_sandbox_restriction_demo.sh
```

---

### E) Run hangs / repeated approvals

Symptom:
- Similar approvals keep showing up; output keeps streaming without progress

Diagnosis:
- Inspect the `approval_requested` / `approval_decided` event sequence
- Ensure there is a working ApprovalProvider (Studio frontend or CLI)

Fix:
- Confirm the frontend actually POSTs `approved/denied`
- Add safe frequent commands to allowlist
- Tune `approval_timeout_ms`

---

### F) Skill mention not applied

Symptom:
- You wrote a mention, but the skill body was not injected

Diagnosis:
- Ensure mention syntax: `$[account:domain].skill_name`
- Ensure session filesystem sources are correct, and skill scan finds the skill

Fix:
- Use a valid mention
- Fix sources and rescan

---

### G) `target_source must be one of session filesystem_sources`

Symptom:
- Studio “create skill” API returns 400

Diagnosis:
- Check current session filesystem sources

Fix:
- `PUT /skills/sources` first, then create the skill

## 9.3 Helpful commands

```bash
# health check
curl -s http://127.0.0.1:8000/api/v1/health | jq .

# pending approvals
curl -s http://127.0.0.1:8000/api/v1/runs/<run_id>/approvals/pending | jq .

# skills preflight/scan (CLI)
PYTHONPATH=packages/skills-runtime-sdk-python/src python3 -m agent_sdk.cli.main skills preflight --workspace-root . --config /tmp/skills.cli.overlay.yaml --pretty
PYTHONPATH=packages/skills-runtime-sdk-python/src python3 -m agent_sdk.cli.main skills scan --workspace-root . --config /tmp/skills.cli.overlay.yaml --pretty
```

## 9.4 Troubleshooting log (recommended)

Keep a worklog in your own project (location is up to you; it does not have to live in this repo). For each incident, at minimum record:

- timestamp
- commands you ran
- the raw error
- the fix you applied
- the outcome

---

Prev: [`08-architecture-internals.md`](./08-architecture-internals.md)  
Next: [`10-cookbook.md`](./10-cookbook.md)
