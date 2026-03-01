<div align="center">

[English](10-cookbook.md) | [中文](10-cookbook.cn.md) | [Help](README.md)

</div>

# 10. Cookbook: integration and rollout recipes

## Recipe 1: local development (low interruption)

Goal: keep developer velocity high, while still maintaining basic safety boundaries.

Recommended:

- `safety.mode=ask`
- allowlist common read-only commands
- denylist destructive commands
- `sandbox.default_policy=restricted` (use a minimal profile; don’t tighten too aggressively at first)

Checklist:

1. Copy an overlay template and adjust as needed:

   ```bash
   cp help/examples/sdk.overlay.yaml /tmp/sdk.overlay.yaml
   ```

2. Provide the API key via environment variables (do not hardcode it in YAML):

   ```bash
   export OPENAI_API_KEY='...'
   ```

3. Run Skills CLI preflight + scan (validate sources + mentions + overlay correctness):

    ```bash
    cp help/examples/skills.cli.overlay.yaml /tmp/skills.cli.overlay.yaml

    PYTHONPATH=packages/skills-runtime-sdk-python/src \
      python3 -m skills_runtime.cli.main skills preflight --workspace-root . --config /tmp/skills.cli.overlay.yaml --pretty

    PYTHONPATH=packages/skills-runtime-sdk-python/src \
      python3 -m skills_runtime.cli.main skills scan --workspace-root . --config /tmp/skills.cli.overlay.yaml --pretty
    ```

4. Run the minimal Python example (validate run + tools/approvals/sandbox baseline flow):

   ```bash
   PYTHONPATH=packages/skills-runtime-sdk-python/src \
     python3 help/examples/run_agent_minimal.py --workspace-root . --config /tmp/sdk.overlay.yaml
   ```

## Recipe 2: staging validation (stability first)

Goal: catch config drift and permission issues early.

Suggestions:

- tighten allowlist (keep only truly frequent safe commands)
- keep denylist conservative
- pin overlay sources (avoid “random” environment overrides)
- run `bash scripts/pytest.sh` before each deployment

## Recipe 3: production deployment (Linux)

Goal: security + observability + rollback capability.

Suggestions:

- `sandbox.default_policy=restricted`
- `os.mode=auto` + `bubblewrap.unshare_net=true`
- approvals must be observable (pending list + decision stream)
- keep a rollback switch: `restricted -> none`

## Recipe 4: frontend product integrates via Studio API

Goal: a product frontend can start runs and handle approvals without understanding internals.

Note:
- Studio MVP is a **downstream example server**; it does **not** define the SDK/framework contract.
- This recipe documents integration with Studio's REST+SSE API. For the full end-to-end guide, see `help/07-studio-guide.md`.

Minimal flow:

1. `POST /api/v1/sessions`
2. `PUT /api/v1/sessions/{session_id}/skills/sources`
3. `POST /api/v1/sessions/{session_id}/runs`
4. Subscribe to SSE events: `GET /api/v1/runs/{run_id}/events/stream`
5. If approvals appear, call `GET /api/v1/runs/{run_id}/approvals/pending`, then `POST /api/v1/runs/{run_id}/approvals/{approval_key}` to submit decisions

## Recipe 5: CI gate

Suggested gates:

1. Tier-0 single entrypoint: `bash scripts/tier0.sh`
2. `skills preflight` (CI overlay)
3. `skills scan` (warnings/errors must be actionable)
4. Docs check (README/Help links should not break; example commands should work)

## Recipe 6: incident drills (bi-weekly)

Drill scenarios:

- missing sandbox adapter
- invalid overlay path
- approval timeout
- sources misconfiguration

Requirements:

- record time-to-recover
- record the real root cause
- update `help/09-troubleshooting.md`

---

Prev: [`09-troubleshooting.md`](./09-troubleshooting.md)  
Next: [`11-faq.md`](./11-faq.md)
