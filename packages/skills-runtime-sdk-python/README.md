<div align="center">

[English](README.md) | [中文](README.cn.md)

</div>

# Skills Runtime SDK (Python)

This folder contains the **Python SDK reference implementation** of Skills Runtime.

If you want the fastest “end-to-end” experience (Runs + SSE + approvals + sandbox + skills management), start from the repo root `README.md` and run the **Studio MVP**.

## Install (PyPI)

PyPI package name: `skills-runtime-sdk` (Python `>=3.10`).

```bash
python -m pip install -U skills-runtime-sdk
```

Optional extras (skills sources):

```bash
python -m pip install -U "skills-runtime-sdk[redis]"
python -m pip install -U "skills-runtime-sdk[pgsql]"
python -m pip install -U "skills-runtime-sdk[all]"
```

Note: the import name is `agent_sdk` (package name differs from module name).

## Dev & tests

From this directory:

- Editable install:
  - `python -m pip install -e ".[dev]"`
- Run tests:
  - `pytest -q`
- Quick import check:
  - `python -c "import agent_sdk; print(agent_sdk.__version__)"`

If you hit `UnicodeDecodeError` on some machines due to locale/encoding, try:

- `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 python ...`

## Local config files

### 1) API key (local only)

1. Copy:
   - `packages/skills-runtime-sdk-python/.env.example` → `packages/skills-runtime-sdk-python/.env`
2. Edit `.env`:
   - `OPENAI_API_KEY=...`

Do not commit `.env` (it is ignored).

### 2) Base URL / model names

1. Copy:
   - `packages/skills-runtime-sdk-python/config/runtime.yaml.example` → `packages/skills-runtime-sdk-python/config/runtime.yaml`
2. Edit `runtime.yaml`:
   - `llm.base_url`: `http(s)://<your-host>/v1`
   - `llm.api_key_env`: default `OPENAI_API_KEY`
   - `models.planner` / `models.executor`: replace with real model names

## Bootstrap (recommended)

`Agent` does not implicitly load `.env` or auto-discover overlays (avoid import-time side effects). For an “easy to run + easy to debug” experience, use bootstrap:

- `agent_sdk.bootstrap.resolve_effective_run_config(...)`

It gives you:

- an effective config (session > env > yaml overlays)
- a `sources` map (where each field came from; useful for UI and troubleshooting)

For details, read `help/02-config-reference.md`.

## Skills CLI

This package ships a CLI (stdlib `argparse`) for skills preflight and scanning:

```bash
skills-runtime-sdk --help
skills-runtime-sdk skills --help
```

