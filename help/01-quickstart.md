<div align="center">

[English](01-quickstart.md) | [中文](01-quickstart.cn.md) | [Help](README.md)

</div>

# 01. Quickstart: Run SDK + Studio in ~20 minutes

## 1.1 Prerequisites

- Python `>=3.10` (required)
- Node.js (Studio frontend only; 20.19+ or 22.12+)
- Run commands from the repo root: `<repo_root>`

Note: if you use Python 3.9, imports may fail due to `X | None` typing.

## 1.2 Run offline regression tests first (validate your environment)

```bash
cd <repo_root>
bash scripts/pytest.sh
```

Expected:
- root tests pass
- SDK tests pass

If it fails, see `help/09-troubleshooting.md` (environment/version section).

## 1.3 Minimal SDK run (Python)

### Step 1: Prepare an overlay config

Copy the example:

```bash
cp help/examples/sdk.overlay.yaml /tmp/sdk.overlay.yaml
```

Edit for your environment:
- `llm.base_url`
- `llm.api_key_env`
- `models.planner`
- `models.executor`

### Step 2: Provide your API key (local only)

```bash
export OPENAI_API_KEY='<your-key>'
```

### Step 3: Run the minimal script

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 help/examples/run_agent_minimal.py --workspace-root . --config /tmp/sdk.overlay.yaml
```

Expected output (example):
- prints a `run_id` (from `run_started`)
- streams event types (at least `run_started`, `run_completed`; may include `tool_call_*`)
- prints `final_output` and `wal_locator` at the end

## 1.4 Minimal Skills CLI validation

### Step 1: Prepare a Skills CLI overlay

```bash
cp help/examples/skills.cli.overlay.yaml /tmp/skills.cli.overlay.yaml
```

### Step 2: preflight

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m skills_runtime.cli.main skills preflight \
  --workspace-root . \
  --config /tmp/skills.cli.overlay.yaml \
  --pretty
```

### Step 3: scan

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m skills_runtime.cli.main skills scan \
  --workspace-root . \
  --config /tmp/skills.cli.overlay.yaml \
  --pretty
```

Expected: JSON on stdout with `ok/issues` or `report/skills`.

## 1.5 Minimal Studio MVP run

### Backend

```bash
cd <repo_root>
cp packages/skills-runtime-studio-mvp/backend/.env.example \
   packages/skills-runtime-studio-mvp/backend/.env

bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

Default URL: `http://127.0.0.1:8000`

Health check:

```bash
curl -s http://127.0.0.1:8000/api/v1/health | jq .
```

### Frontend

In another terminal:

```bash
cd <repo_root>
npm -C packages/skills-runtime-studio-mvp/frontend install
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

Default URL: `http://localhost:5173`

## 1.6 First interaction suggestions

1. Create a Session
2. Check / set `filesystem_sources`
3. In Run, send a message with a valid mention, e.g.:

```text
Please call $[web:mvp].article-writer and write ~300 words about “why offline regression tests matter”.
```

## 1.7 Quick checklist

- [ ] Python >= 3.10
- [ ] `scripts/pytest.sh` passes
- [ ] minimal SDK script runs
- [ ] `skills preflight/scan` returns JSON
- [ ] Studio backend health OK
- [ ] Studio frontend can create session and run

## 1.8 Next

- Config: `help/02-config-reference.md`
- Python API: `help/03-sdk-python-api.md`
- CLI: `help/04-cli-reference.md`

---

Prev: [00. Overview](00-overview.md) · Next: [02. Config Reference](02-config-reference.md)
