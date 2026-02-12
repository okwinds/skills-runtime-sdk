<div align="center">

[中文](README.cn.md) | [English](README.md)

</div>

# Skills Runtime Studio MVP — 前端

Studio MVP 的 React + Vite 前端。

这是仓库内的示例工程（不是发布到 npm 的包），需要从 monorepo 源码启动。

## 前置条件

- Node.js **20.19+** 或 **22.12+**

## 快速启动

```bash
cd <repo_root>
npm -C packages/skills-runtime-studio-mvp/frontend install
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

浏览器打开：`http://localhost:5173`

说明：开发服务器会将 `/api` 与 `/studio/api` 代理到 `http://localhost:8000`（见 `vite.config.ts`）。

## 常用脚本

- `npm -C packages/skills-runtime-studio-mvp/frontend run dev`
- `npm -C packages/skills-runtime-studio-mvp/frontend test`
- `npm -C packages/skills-runtime-studio-mvp/frontend run lint`
- `npm -C packages/skills-runtime-studio-mvp/frontend run build`

## License

Apache-2.0（见仓库根目录 `LICENSE`）

