<div align="center">

[English](07-studio-guide.md) | [中文](07-studio-guide.cn.md) | [Help](README.md)

</div>

# 07. Studio MVP: Sessions, Skills, Runs, Approvals — End-to-End

## 7.1 Components

- Backend: `packages/skills-runtime-studio-mvp/backend`
- Frontend: `packages/skills-runtime-studio-mvp/frontend`
- Backend protocol: REST + SSE

## 7.2 Start the backend

```bash
cd <repo_root>
cp packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example \
   packages/skills-runtime-studio-mvp/backend/config/runtime.yaml

cp packages/skills-runtime-studio-mvp/backend/.env.example \
   packages/skills-runtime-studio-mvp/backend/.env

bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

Offline regression (fake LLM, no real key/network):

```bash
cd <repo_root>
STUDIO_LLM_BACKEND=fake bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

Notes:
- `STUDIO_LLM_BACKEND=fake` makes the backend use a deterministic `FakeChatBackend` to exercise tool_calls → approvals → completed, suitable for CI/offline integration tests.

Health check:

```bash
curl -s http://127.0.0.1:8000/api/v1/health | jq .
```

## 7.3 Start the frontend

```bash
cd <repo_root>
npm -C packages/skills-runtime-studio-mvp/frontend install
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

## 7.4 API overview

- `GET /api/v1/health`
- `GET /api/v1/sessions`
- `POST /api/v1/sessions`
- `DELETE /api/v1/sessions/{session_id}`
- `PUT /api/v1/sessions/{session_id}/skills/roots`
- `GET /api/v1/sessions/{session_id}/skills`
- `POST /studio/api/v1/sessions/{session_id}/skills`
- `POST /api/v1/sessions/{session_id}/runs`
- `GET /api/v1/runs/{run_id}/events/stream`
- `GET /api/v1/runs/{run_id}/approvals/pending`
- `POST /api/v1/runs/{run_id}/approvals/{approval_key}`

## 7.5 End-to-end flow (curl)

### 1) Create a session

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"title":"demo"}' | jq .
```

Notes:
- If `skills_roots` is `null` (or omitted), the backend will backfill a default root (generated root).
- If you pass `[]`, it means “explicitly empty roots”, and creating skills will fail by design (safety constraint).

### 2) Set skill roots (optional)

```bash
SESSION_ID='<session_id>'
WORKSPACE_ROOT="$(curl -s http://127.0.0.1:8000/api/v1/health | jq -r .workspace_root)"
ROOT="${WORKSPACE_ROOT}/.skills_runtime_sdk/skills"

curl -s -X PUT "http://127.0.0.1:8000/api/v1/sessions/${SESSION_ID}/skills/roots" \
  -H 'Content-Type: application/json' \
  -d "{\"roots\":[\"${ROOT}\"]}" | jq .
```

### 3) Create a file-based skill

```bash
curl -s -X POST "http://127.0.0.1:8000/studio/api/v1/sessions/${SESSION_ID}/skills" \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"article-writer",
    "description":"writing skill",
    "body_markdown":"# Output structure\n- Title\n- Bullet points",
    "title":"Article Writer"
  }' | jq .
```

### 4) Start a run

```bash
RUN_ID=$(curl -s -X POST "http://127.0.0.1:8000/api/v1/sessions/${SESSION_ID}/runs" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Please call $[web:mvp].article-writer and output 5 test suggestions."}' | jq -r .run_id)

echo "$RUN_ID"
```

### 5) Subscribe to SSE events

```bash
curl -N "http://127.0.0.1:8000/api/v1/runs/${RUN_ID}/events/stream"
```

### 6) Handle approvals (if any)

```bash
curl -s "http://127.0.0.1:8000/api/v1/runs/${RUN_ID}/approvals/pending" | jq .

APPROVAL_KEY='<approval_key>'
curl -s -X POST "http://127.0.0.1:8000/api/v1/runs/${RUN_ID}/approvals/${APPROVAL_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approved"}' | jq .
```

## 7.6 Common configuration touchpoints

- `packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example`: models / safety / sandbox (copy to `runtime.yaml` locally; do not commit)
- `packages/skills-runtime-studio-mvp/backend/.env`: API key env vars (local only)
- `STUDIO_WORKSPACE_ROOT`: override backend workspace root (for test isolation / multi-instance)
- `skills.env_var_missing_policy`: Studio backend does not inject a HumanIOProvider by default, so when a skill requires a missing env var, prefer `fail_fast` for deterministic failure (`run_failed.error_kind=missing_env_var` with structured details). The example `runtime.yaml.example` in this repo defaults to `fail_fast`.

## 7.7 Regression entrypoints

```bash
bash packages/skills-runtime-studio-mvp/backend/scripts/pytest.sh
npm -C packages/skills-runtime-studio-mvp/frontend test
npm -C packages/skills-runtime-studio-mvp/frontend run lint
```

## 7.8 Recommendations

1. Keep session roots within controlled directories
2. Use `/approvals/pending` for reconnect/recovery
3. Keep `events.jsonl` for forensics/debugging

## 7.9 Frontend Info Dock (tabs under Output)

To avoid too many fragmented panels under Output, the Studio Run page uses a single **Info Dock** (collapsed by default) with tabs:

- `Status`: run status and key hints (e.g., approvals pending)
- `History`: past final outputs in the same session (truncated)
- `Logs`: SSE timeline (includes token/text delta summaries)
- `Approvals`: pending approvals list + timeline
- `Sandbox`: per-tool `data.sandbox` metadata (3 views: Summary / Per-Tool / Last Fail)
- `Config`: `run_started.config_summary` (models/llm/overlays/sources)

Fast troubleshooting path:

1) Check `Approvals`: do you see `approval_requested` waiting for a decision?  
2) Check `Sandbox → Per-Tool`: do you see `active=true`, and expected `effective/adapter`?  
3) To prove “real OS sandbox restrictions” (not just approvals), run: `bash scripts/integration/os_sandbox_restriction_demo.sh`

---

Prev: [`06-tools-and-safety.md`](./06-tools-and-safety.md)  
Next: [`08-architecture-internals.md`](./08-architecture-internals.md)
