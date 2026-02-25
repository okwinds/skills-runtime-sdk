# Cheatsheet (English)

Purpose: give a coding agent a runnable “happy path” + key entrypoints.

## Core rules (do not bypass)

- Doc/spec first: write down `Goal/AC/Test Plan` (PR/issue or your internal doc is fine; OSS build does not depend on internal collaboration artifacts).
- TDD gate: “done” means offline regression passed (at least `bash scripts/pytest.sh`).
- Reproducibility: no secrets in repo; examples must be runnable.

## Entry points

- Help (integration/ops manual): `help/README.md`
- Coding-agent docs index: `docs_for_coding_agent/DOCS_INDEX.md`
- Examples library (offline-by-default, teaching/coverage): `docs_for_coding_agent/examples/`
- Human-facing app examples: `examples/apps/`

## Offline verification

```bash
bash scripts/pytest.sh
```

Example smoke tests only:

```bash
pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py
```

Tier-0 CI-equivalent gate:

```bash
bash scripts/tier0.sh
```

## Optional: real model run

```bash
cp help/examples/sdk.overlay.yaml /tmp/sdk.overlay.yaml
export OPENAI_API_KEY='...'
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 help/examples/run_agent_minimal.py --workspace-root . --config /tmp/sdk.overlay.yaml
```

## Docker + sandbox notes

See: `help/sandbox-best-practices.md` (section “Docker/container notes”).

## Optional (internal): strict local-docs enforcement

```bash
REQUIRE_LOCAL_DOCS=1 bash scripts/tier0.sh
```

See: `help/12-validation-suites.md` (section “Internal enforcement”).
