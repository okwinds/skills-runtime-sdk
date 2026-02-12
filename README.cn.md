<div align="center">

[中文](README.cn.md) | [English](README.md)

</div>

# Skills Runtime SDK

_许可证：Apache License 2.0（见 `LICENSE`）_

一套 **Skills-first** 的 Agent Runtime SDK（Python）+ 配套 **Studio MVP**（最小示例），用于：

- 用标准化的 Skills（`SKILL.md`）组织能力与资产
- 以 Runs + SSE 事件流的方式可观测地运行
- 用 `approvals`（门卫）+ `OS sandbox`（围栏）降低高风险工具调用带来的事故概率

---

## 快速开始（5 分钟体验 Studio MVP）

### 0) 前置条件

- Python **3.10+**
- Node.js（用于前端；建议 18+）

### 1) 配置

```bash
cd <repo_root>

# 1) 后端环境变量（key 不要提交）
cp packages/skills-runtime-studio-mvp/backend/.env.example \
   packages/skills-runtime-studio-mvp/backend/.env

# 2) 运行时 overlay（本地敏感文件；远端只保留 .example）
cp packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example \
   packages/skills-runtime-studio-mvp/backend/config/runtime.yaml
```

然后编辑：

- `packages/skills-runtime-studio-mvp/backend/.env`：填 `OPENAI_API_KEY`
- `packages/skills-runtime-studio-mvp/backend/config/runtime.yaml`：填 `llm.base_url`、`models.planner/executor`

### 2) 启动后端

```bash
bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

健康检查：

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

### 3) 启动前端

```bash
npm -C packages/skills-runtime-studio-mvp/frontend install
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

浏览器打开：`http://localhost:5173`

### 4) 在 Run 里试一个最小任务

直接在 UI 的 Run 输入框里输入普通中文任务即可。

Studio MVP 启动时会默认安装两份内置示例技能（用于开箱体验）：

- `$[web:mvp].article-writer`
- `$[web:mvp].novel-writer`

如果你需要“系统间”显式调用某个 skill，用合法 mention：

```text
$[web:mvp].skill_name
```

说明：框架只提取**合法** mention，其它类似片段一律当普通文本处理（不会打断 run）。

---

## Help 导览（建议按顺序）

- 总导航：`help/README.cn.md`
- 20 分钟跑通：`help/01-quickstart.cn.md`
- 配置全集：`help/02-config-reference.cn.md`
- Tools + Safety（approvals + sandbox）：`help/06-tools-and-safety.cn.md`
- Studio 端到端手册：`help/07-studio-guide.cn.md`
- 故障排查：`help/09-troubleshooting.cn.md`

---

## 如何确认“真沙箱生效”（不与 approvals 混淆）

不要凭“输出里出现绝对路径”判断（macOS seatbelt 不会虚拟路径）。建议用可复现验证：

```bash
bash scripts/integration/os_sandbox_restriction_demo.sh
```

并在 Studio UI 的 `Info → Sandbox` 中查看每次工具返回的证据字段：
`tool_call_finished.result.data.sandbox.active/adapter/effective`。

---

## 回归（离线）

```bash
bash scripts/pytest.sh
```

---

## 鸣谢

- Codex CLI（OpenAI）：`https://github.com/openai/codex`
