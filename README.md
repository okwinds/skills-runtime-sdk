<div align="center">

[English](README.md) | [中文](README.cn.md)

</div>

# Skills Runtime SDK

_License: Apache License 2.0 (see `LICENSE`)_

A **skills-first** Agent Runtime SDK (Python) plus a companion **Studio MVP** (minimal example), designed for:

- Organizing reusable capabilities as standardized Skills (`SKILL.md`)
- Running with observable **Runs + SSE event streams**
- Reducing risk of tool execution with `approvals` (gatekeeper) + **OS sandbox** (fence)

---

## Quickstart (Try Studio MVP in ~5 minutes)

### 0) Prerequisites

- Python **3.10+**
- Node.js (for the frontend; recommended 18+)

### 1) Configure (local only, do not commit)

```bash
cd <repo_root>

# 1) Backend env (do NOT commit API keys)
cp packages/skills-runtime-studio-mvp/backend/.env.example \
   packages/skills-runtime-studio-mvp/backend/.env

# 2) Runtime overlay (local sensitive file; only `.example` is kept in the repo)
cp packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example \
   packages/skills-runtime-studio-mvp/backend/config/runtime.yaml
```

Then edit:

- `packages/skills-runtime-studio-mvp/backend/.env`: set `OPENAI_API_KEY`
- `packages/skills-runtime-studio-mvp/backend/config/runtime.yaml`: set `llm.base_url` and `models.planner/executor`

### 2) Start the backend

```bash
bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

Health check:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

### 3) Start the frontend

```bash
npm -C packages/skills-runtime-studio-mvp/frontend install
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

Open: `http://localhost:5173`

### 4) Run a minimal task

You can type a normal task in the UI. Studio MVP also installs two built-in example skills by default:

- `$[web:mvp].article-writer`
- `$[web:mvp].novel-writer`

If you need an explicit skill call (system-to-system), use a valid mention:

```text
$[account:domain].skill_name
```

Note: only **valid** mentions are extracted; invalid “mention-like” fragments are treated as plain text and will not interrupt the run.

---

## Help (recommended reading order)

- Index: `help/README.md` (English) / `help/README.cn.md` (中文)
- Quickstart: `help/01-quickstart.md`
- Config reference: `help/02-config-reference.md`
- Tools + Safety (approvals + sandbox): `help/06-tools-and-safety.md`
- Studio end-to-end: `help/07-studio-guide.md`
- Troubleshooting: `help/09-troubleshooting.md`

---

## How to verify “real sandbox” (not just approvals)

Do not rely on “absolute paths in output” (macOS seatbelt does not virtualize paths). Use the reproducible demo:

```bash
bash scripts/integration/os_sandbox_restriction_demo.sh
```

In Studio, check `Info → Sandbox` and inspect evidence fields:
`tool_call_finished.result.data.sandbox.active/adapter/effective`.

---

## Offline tests

```bash
bash scripts/pytest.sh
```

---

## Acknowledgements

- Codex CLI (OpenAI): `https://github.com/openai/codex`
