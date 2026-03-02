<div align="center">

[English](12-validation-suites.md) | [中文](12-validation-suites.cn.md) | [Help](README.md)

</div>

# 12. Validation Suite Index: Test Tiers (Tier-0/1/2) and Evidence

> Goal: hard-separate deterministic offline CI gates from real-environment validation, so you can iterate without requiring a real LLM for every regression.

## 12.1 Tier definitions (must-follow)

| Tier | Goal | Constraints (must) | In CI gate |
|---|---|---|---|
| Tier-0 (deterministic) | Offline, repeatable regression gate | No network; no real API keys; no reliance on real LLM randomness | Yes |
| Tier-1 (integration) | Verify environment capabilities | Can depend on OS capabilities (`sandbox-exec` / `bwrap`); may skip when unavailable | Nightly or manual |
| Tier-2 (real env) | Real provider quality check | Can depend on network, real keys, real providers | Nightly/manual only |

## 12.2 Tier-0: deterministic offline gate (recommended single entrypoint)

Recommended gate script:

```bash
bash scripts/tier0.sh
```

What it runs:

1. Repo + SDK Python unit tests (including examples smoke):

   ```bash
   bash scripts/pytest.sh
   ```

2. Studio backend offline E2E (fake LLM + approvals regression):

   ```bash
   bash packages/skills-runtime-studio-mvp/backend/scripts/pytest.sh
   ```

3. Studio frontend unit tests (UI/state machine/metadata notices):

   ```bash
   npm -C packages/skills-runtime-studio-mvp/frontend ci
   npm -C packages/skills-runtime-studio-mvp/frontend test
   ```

Evidence to archive (recommended in CI):
- pytest outputs (pass/skip/fail summary)
- events/WAL artifacts if your test setup emits them (optional, project-dependent)

### 12.2.1 Internal production: enforce local collaboration docs without exposing them

Notes:
- This repo may intentionally exclude local collaboration artifacts (for example: the collaboration constitution, the doc index, worklog / task summaries) via `.gitignore` to avoid exposing them in OSS.
- Internal production environments may still want to enforce their presence as part of a gate.

Approach (explicit switch):
- Default (OSS/public CI): do not require these local docs to exist.
- Internal enforcement: set `REQUIRE_LOCAL_DOCS=1` to enable strict checks in smoke tests.

Example:

```bash
REQUIRE_LOCAL_DOCS=1 bash scripts/tier0.sh
```

This switch only controls whether the tests enforce the docs’ presence; it does not change SDK runtime behavior.

## 12.3 Tier-1: integration validation (optional; safe to skip)

OS sandbox "visible restriction" checks (offline; adapters may be unavailable):

```bash
# human-visible sandbox effect demo (macOS/Linux)
bash scripts/integration/os_sandbox_restriction_demo.sh

# sandbox.profile macro expansion + evidence output (macOS/Linux; prints skipped when adapters are missing)
bash scripts/integration/sandbox_profile_regression.sh dev
bash scripts/integration/sandbox_profile_regression.sh balanced
bash scripts/integration/sandbox_profile_regression.sh prod
```

Notes:
- Tier-1 validates "dependencies exist + restrictions are real", not as a hard gate.
- Avoid putting `sandbox-exec`/`bwrap`-dependent checks into the default CI gate.

## 12.4 Tier-2: real environment validation (nightly/manual)

Suggested suite (examples):
- Run `help/examples/run_agent_minimal.py` against a real provider (real base_url + real key)
- Run skills preflight/scan against real roots/spaces/sources
- Validate bubblewrap availability in the production Linux/container environment

Constraints:
- Declare external dependencies explicitly (network, secrets, container privileges, service endpoints)
- Do not block the default deterministic gate

---

Back to overview: `help/README.md`  
Related: `help/10-cookbook.md`
