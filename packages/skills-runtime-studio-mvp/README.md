<div align="center">

[English](README.md) | [中文](README.cn.md)

</div>

# Skills Runtime Studio MVP

A minimal “Studio” built on top of `skills-runtime-sdk`, providing:

- **File-based Skill creation**: create a skill directory + `SKILL.md` under a configured root
- **Runs + SSE event streaming**: stream run events and results from the backend
- **React UI (Vite)**: Sessions / Skills / Create / Run in one place

This package lives inside the monorepo at `packages/skills-runtime-studio-mvp/` and reuses the SDK source via `PYTHONPATH`.

## Quick start

Prerequisites:

- Python `>=3.10`
- Node.js **20.19+** or **22.12+** (frontend only)

Config (local only; do not commit secrets):

```bash
cd <repo_root>
cp packages/skills-runtime-studio-mvp/backend/.env.example \
   packages/skills-runtime-studio-mvp/backend/.env

cp packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example \
   packages/skills-runtime-studio-mvp/backend/config/runtime.yaml
```

Then edit:

- `packages/skills-runtime-studio-mvp/backend/.env`: set `OPENAI_API_KEY`
- `packages/skills-runtime-studio-mvp/backend/config/runtime.yaml`: set `llm.base_url` and `models.planner/executor`

Start backend:

```bash
bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

Start frontend:

```bash
npm -C packages/skills-runtime-studio-mvp/frontend install
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

Open: `http://localhost:5173`

## Docs

- Studio end-to-end: `help/07-studio-guide.md` / `help/07-studio-guide.cn.md`
- Troubleshooting: `help/09-troubleshooting.md` / `help/09-troubleshooting.cn.md`
- Sandbox best practices: `help/sandbox-best-practices.md` / `help/sandbox-best-practices.cn.md`

## Offline regression

```bash
bash packages/skills-runtime-studio-mvp/backend/scripts/pytest.sh
npm -C packages/skills-runtime-studio-mvp/frontend test
```

