<div align="center">

[English](README.md) | [中文](README.cn.md)

</div>

# Skills Runtime Studio MVP — Frontend

React + Vite frontend for the Studio MVP.

This is a repo example (not a published npm package). Start it from the monorepo root.

## Prerequisites

- Node.js **20.19+** or **22.12+**

## Quick start

```bash
cd <repo_root>
npm -C packages/skills-runtime-studio-mvp/frontend install
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

Open: `http://localhost:5173`

The dev server proxies backend APIs to `http://localhost:8000` (see `vite.config.ts`).

## Scripts

- `npm -C packages/skills-runtime-studio-mvp/frontend run dev`
- `npm -C packages/skills-runtime-studio-mvp/frontend test`
- `npm -C packages/skills-runtime-studio-mvp/frontend run lint`
- `npm -C packages/skills-runtime-studio-mvp/frontend run build`

## License

Apache-2.0 (see repo root `LICENSE`)
