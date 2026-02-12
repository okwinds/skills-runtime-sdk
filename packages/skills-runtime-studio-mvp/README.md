# Skills Runtime Studio MVP

基于 `skills-runtime-sdk` 的原型 Studio（MVP），提供：

- **文件级 Skill 创建**：在指定 skills root 下落盘创建 skill 目录与 `SKILL.md`
- **Runs + SSE 流式事件**：通过后端 SSE 接口流式返回运行事件与结果
- **React UI（Vite）**：Sessions / Skills / Create / Run 一体化操作界面

本目录已迁入 `skills-runtime-sdk` monorepo：路径为 `packages/skills-runtime-studio-mvp/`，通过 `PYTHONPATH` 复用同仓库内的 Python SDK 实现（见下方后端启动命令与测试脚本）。

## 核心能力

- Session 管理：创建/列出 session，并为 session 配置 skills roots
- Skills 管理：列出 session 下可用 skills；在可写 root 下创建新 skill
- Runs：在指定 session 上创建 run，并通过 SSE 拉取 run events 流
- 离线可回归：后端测试脚本固定设置 UTF-8 与 `PYTHONPATH`，避免环境漂移

## 架构概览

- **Backend（FastAPI）**
  - 复用上游 `skills-runtime-sdk` 的 Python SDK 能力（Skill 发现/运行、session 数据落盘等）
  - Studio 后端路由：`backend/src/studio_api/*`
  - 关键入口：`backend/src/studio_api/app.py`
- **Frontend（React + Vite）**
  - API 封装：`frontend/src/lib/api.ts`（`fetch` + SSE stream 解析）
  - SSE 文本解析器：`frontend/src/lib/sse.ts`
  - 主界面：`frontend/src/App.tsx`（Tabs：Skills / Create / Run）

## 快速开始

### 前置条件

- 本 MVP 位于 `skills-runtime-sdk` 仓库内：`packages/skills-runtime-studio-mvp/`
- 本 MVP 后端复用同仓库内的 Python SDK 源码：
  - `packages/skills-runtime-sdk-python/src`
- Python 环境可运行 `uvicorn` / `pytest`（如缺失 `uvicorn`：`python -m pip install "uvicorn[standard]"`）
- Node.js 环境可运行前端 `npm` 脚本

### 配置（本仓库内收敛）

- LLM overlay（示例模板）：`backend/config/runtime.yaml.example`（复制为本地 `backend/config/runtime.yaml` 使用；不要提交到远端仓库）
- 环境变量示例：`backend/.env.example`
- Studio 详细操作手册：`help/07-studio-guide.md`
- 故障排查手册：`help/09-troubleshooting.md`
- 沙箱/审批最佳实践：`help/sandbox-best-practices.zh-CN.md`（English: `sandbox-best-practices.en.md`）

推荐做法（不提交 secrets）：

```bash
cd <repo_root>
cp packages/skills-runtime-studio-mvp/backend/.env.example packages/skills-runtime-studio-mvp/backend/.env
```

然后编辑 `packages/skills-runtime-studio-mvp/backend/.env` 填入 `OPENAI_API_KEY`（仅本机使用，不要提交）。

### 启动后端（推荐：一键脚本）

从仓库根目录执行：

```bash
cd <repo_root>
bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

说明：
- 脚本会自动设置 `PYTHONPATH`（指向 `packages/skills-runtime-sdk-python/src` + `packages/skills-runtime-studio-mvp/backend/src`）并启动 `uvicorn`。
- 后端工作区（workspace root）为 `packages/skills-runtime-studio-mvp/backend/`，运行产物会落在：`packages/skills-runtime-studio-mvp/backend/.skills_runtime_sdk/`（已在 `.gitignore` 中忽略）。
- 默认 skills roots 为 `packages/skills-runtime-studio-mvp/backend/.skills_runtime_sdk/skills`，UI 首次打开即可看到 skills（无需手动“Apply Roots/生效”）。

#### 启动排障：`env file not found`

若你看到类似错误：

```text
ValueError: env file not found: <some-path>/.env
```

通常是因为你的 shell 环境里遗留了以下环境变量，且指向了一个已不存在的文件（例如历史目录已移动/删除）：

- `SKILLS_RUNTIME_SDK_ENV_FILE`（新）
- `AGENT_SDK_ENV_FILE`（旧兼容）

解决方式：

- 推荐：直接 `unset` 再重试启动
  - `unset SKILLS_RUNTIME_SDK_ENV_FILE AGENT_SDK_ENV_FILE`
- 或者：把它们改成一个存在的 `.env` 路径（相对路径会以 `backend/` 为锚点解析）

#### 运行排障：`overlay config not found`

若你在 Run 的 Output 里看到类似错误：

```text
[run_failed] ValueError: overlay config not found: <some-path>/config/runtime.yaml
```

这通常意味着你的 shell 环境里遗留了以下环境变量，指向了一个已不存在/已移动的 overlay 文件路径（例如历史目录已移动/删除）：

- `SKILLS_RUNTIME_SDK_CONFIG_PATHS`（新）
- `AGENT_SDK_CONFIG_PATHS`（旧兼容）

解决方式：

- 推荐：直接 `unset` 后重启后端
  - `unset SKILLS_RUNTIME_SDK_CONFIG_PATHS AGENT_SDK_CONFIG_PATHS`
- 或者：把它们改成一个存在的 overlay 文件路径（相对路径以 `backend/` 为锚点解析）

### 启动后端（可选：手动启动）

```bash
export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1
export PYTHONPATH="<repo_root>/packages/skills-runtime-sdk-python/src:<repo_root>/packages/skills-runtime-studio-mvp/backend/src:${PYTHONPATH:-}"
cd "<repo_root>/packages/skills-runtime-studio-mvp/backend" && python -m uvicorn studio_api.app:app --reload --host 127.0.0.1 --port 8000
```

### 启动前端

```bash
# 如未安装依赖
npm -C packages/skills-runtime-studio-mvp/frontend install

# 启动开发服务器
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

浏览器打开：`http://localhost:5173`

### Run 里如何引用 Skill（重要）

本 MVP 使用 Skills V2 mention 语法（旧的 `$name`、`[$name](path)` 会报错）：

```text
$[web:mvp].skill_name
```

示例：

```text
请按 $[web:mvp].article-writer 写一篇 500 字科普文章：为什么需要单元测试？要有标题和要点列表。
```

## 验证命令（离线回归入口）

```bash
# 后端测试（包含 PYTHONPATH/UTF-8 等环境设置）
bash packages/skills-runtime-studio-mvp/backend/scripts/pytest.sh

# 前端测试 / 规范 / 构建
npm -C packages/skills-runtime-studio-mvp/frontend test
npm -C packages/skills-runtime-studio-mvp/frontend run lint
npm -C packages/skills-runtime-studio-mvp/frontend run build
```

## 可作废资源（不影响本 MVP 运行）

当你使用本仓库内的 `backend/config/runtime.yaml.example` 与 `backend/.env.example` 后：
- 不需要依赖或编辑其它仓库目录下的 `.env`/LLM overlay；本仓库的后端配置已收敛在 `backend/` 内。

当你使用 `backend/scripts/dev.sh` 启动后端且示例 skills 已自动安装到 `backend/.skills_runtime_sdk/skills` 后：
- 不需要手动把任何外部 skills root 加到 roots；UI 默认会直接展示可用 skills（无需“Apply Roots/手动生效”）。

此外，本仓库已提供：
- `backend/.env.example`：不再需要为了“能跑通 Studio MVP”去依赖 SDK 目录中的 env 示例
- `backend/config/runtime.yaml.example`：不再需要设置 `SKILLS_RUNTIME_SDK_CONFIG_PATHS` 指向 SDK 目录中的 overlay（将其复制为本地 `backend/config/runtime.yaml` 使用；注意 `runtime.yaml` 是本地敏感文件，远端只保留 `.example`）
