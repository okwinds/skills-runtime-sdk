<div align="center">

[中文](07-studio-guide.cn.md) | [English](07-studio-guide.md) | [Help](README.cn.md)

</div>

# 07. Studio MVP：会话、技能、运行、审批全流程

## 7.1 组件说明

- 后端：`packages/skills-runtime-studio-mvp/backend`
- 前端：`packages/skills-runtime-studio-mvp/frontend`
- 后端对外协议：REST + SSE

## 7.2 后端启动

```bash
cd <repo_root>
cp packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example \
   packages/skills-runtime-studio-mvp/backend/config/runtime.yaml

cp packages/skills-runtime-studio-mvp/backend/.env.example \
   packages/skills-runtime-studio-mvp/backend/.env

bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

离线回归（fake LLM，无需真实 key/外网）：

```bash
cd <repo_root>
STUDIO_LLM_BACKEND=fake bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

说明：
- `STUDIO_LLM_BACKEND=fake` 会让后端用 `FakeChatBackend` 产出确定性的 tool_calls→approvals→completed 流程，用于 CI/离线集成回归护栏。

健康检查：

```bash
curl -s http://127.0.0.1:8000/api/v1/health | jq .
```

## 7.3 前端启动

```bash
cd <repo_root>
npm -C packages/skills-runtime-studio-mvp/frontend install
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

## 7.4 API 总览

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

## 7.5 端到端操作（curl 版）

### 1) 创建 session

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"title":"demo"}' | jq .
```

说明：
- 若 `skills_roots` 传 `null`（或不传），后端会自动回填默认 roots（generated root）。
- 若你传 `[]`，表示“显式为空”，后续创建 skill 会因为 roots 为空而报错（这是有意的安全约束）。

### 2) 设置 roots

```bash
SESSION_ID='<session_id>'
WORKSPACE_ROOT="$(curl -s http://127.0.0.1:8000/api/v1/health | jq -r .workspace_root)"
ROOT="${WORKSPACE_ROOT}/.skills_runtime_sdk/skills"

curl -s -X PUT "http://127.0.0.1:8000/api/v1/sessions/${SESSION_ID}/skills/roots" \
  -H 'Content-Type: application/json' \
  -d "{\"roots\":[\"${ROOT}\"]}" | jq .
```

### 3) 创建 skill

```bash
curl -s -X POST "http://127.0.0.1:8000/studio/api/v1/sessions/${SESSION_ID}/skills" \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"article-writer",
    "description":"写作技能",
    "body_markdown":"# 产出结构\n- 标题\n- 要点",
    "title":"Article Writer"
  }' | jq .
```

### 4) 发起 run

```bash
RUN_ID=$(curl -s -X POST "http://127.0.0.1:8000/api/v1/sessions/${SESSION_ID}/runs" \
  -H 'Content-Type: application/json' \
  -d '{"message":"请调用 $[web:mvp].article-writer 输出 5 条测试建议"}' | jq -r .run_id)

echo "$RUN_ID"
```

### 5) 订阅 SSE

```bash
curl -N "http://127.0.0.1:8000/api/v1/runs/${RUN_ID}/events/stream"
```

### 6) 处理审批（如出现）

```bash
curl -s "http://127.0.0.1:8000/api/v1/runs/${RUN_ID}/approvals/pending" | jq .

APPROVAL_KEY='<approval_key>'
curl -s -X POST "http://127.0.0.1:8000/api/v1/runs/${RUN_ID}/approvals/${APPROVAL_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approved"}' | jq .
```

## 7.6 Studio 常见配置点

- `backend/config/runtime.yaml.example`：模型、safety、sandbox（复制为本地 `backend/config/runtime.yaml` 使用；不要提交到远端仓库）
- `backend/.env`：API key 与环境变量
- `STUDIO_WORKSPACE_ROOT`：覆盖后端工作区（测试隔离）
- `skills.env_var_missing_policy`：Studio backend 默认不注入 HumanIOProvider，因此当 skill 依赖 env var 缺失时，建议使用 `fail_fast` 获得确定性失败（终态 `run_failed.error_kind=missing_env_var`，并携带结构化 details）。本仓库示例 `runtime.yaml.example` 已默认设置为 `fail_fast`。

## 7.7 Studio 回归入口

```bash
bash packages/skills-runtime-studio-mvp/backend/scripts/pytest.sh
npm -C packages/skills-runtime-studio-mvp/frontend test
npm -C packages/skills-runtime-studio-mvp/frontend run lint
```

## 7.8 推荐实践

1. 把 session 的 roots 固定在受控目录
2. 使用 pending approvals 接口做断线恢复
3. 保留 `events.jsonl` 作为排障依据

## 7.9 前端 Info Dock（Output 下方信息面板）

为避免 Output 下方出现太多碎片化面板，Studio Run 页把信息区收敛成一个 **Info Dock**（默认隐藏），并用 Tabs 切换：

- `Status`：run 状态与关键提示（例如需要你处理 approvals）
- `History`：同一 session 的历史终态输出（截断展示）
- `Logs`：SSE timeline（包含 token/text delta 摘要）
- `Approvals`：待处理 approvals 列表 + 时间线
- `Sandbox`：工具执行的 `data.sandbox` 元信息（含 3 种视图：Summary / Per-Tool / Last Fail）
- `Config`：`run_started.config_summary`（models/llm/overlays/sources）

如何用它排障（最短路径）：

1) 先看 `Approvals`：是否有 `approval_requested` 需要你点允许/拒绝  
2) 再看 `Sandbox → Per-Tool`：是否出现 `active=true`，以及 `effective/adapter` 是否符合预期  
3) 若要确认“限制真的生效”，跑一次本仓库的验证脚本：`bash scripts/integration/os_sandbox_restriction_demo.sh`

---

上一章：[`06-tools-and-safety.cn.md`](./06-tools-and-safety.cn.md)  
下一章：[`08-architecture-internals.cn.md`](./08-architecture-internals.cn.md)
