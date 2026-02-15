# Cheatsheet (English)

Purpose: give a coding agent a runnable “happy path” + key entrypoints.

## Core rules (do not bypass)

- Doc/spec first: follow `AGENTS.md`.
- TDD gate: “done” means offline regression passed (at least `bash scripts/pytest.sh`).
- Reproducibility: no secrets in repo; examples must be runnable.

## Entry points

- Repo docs index: `DOCS_INDEX.md`
- Backlog (future + done memo): `docs/backlog.md`
- Worklog (commands + results): `docs/worklog.md`
- SDK specs entry: `docs/specs/skills-runtime-sdk/README.md`
- Help (integration/ops manual): `help/README.md`

## Offline verification

```bash
bash scripts/pytest.sh
```

Example smoke tests only:

```bash
pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py
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

