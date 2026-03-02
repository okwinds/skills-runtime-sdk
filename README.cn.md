<div align="center">

[中文](README.cn.md) | [English](README.md)

</div>

# Skills Runtime SDK

_许可证：Apache License 2.0（见 `LICENSE`）_

一套 **Skills-first** 的 Agent Runtime SDK（Python）+ 配套 **Studio MVP**（最小示例），用于：

- 用标准化的 Skills（`SKILL.md`）组织能力与资产
- 以 Runs + SSE 事件流的方式可观测地运行
- 用 `approvals`（门卫）+ `OS sandbox`（围栏）降低高风险工具调用带来的事故概率

概念模型：
- Skills 是一等扩展面（你主要编写/复用的资产）。
- `Agent` API 是运行引擎（负责执行一次 run，并安全编排 tool calls + 落 WAL 事件）。

---

## 架构速览

```text
┌──────────────┐
│ 启动阶段      │  workspace_root + .env + runtime.yaml overlays
└──────┬───────┘
       │ config loader（pydantic 校验 + sources map）
       v
┌──────────────┐     ┌────────────────┐
│ Agent API     │────▶ PromptManager   │───（skills 注入 / history compaction）
│ skills_runtime/core/agent.py │     └────────────────┘
└──────┬───────┘
       │ stream chat events
       v
┌──────────────┐
│ LLM backend   │  fake backend（离线）/ OpenAI-compatible backend
└──────┬───────┘
       │ tool_calls
       v
┌──────────────────────────────────────────────┐
│ Tool orchestration                            │
│ approvals gate  →  OS sandbox  →  exec session │
└──────────────────────────────────────────────┘
       │
       v
┌──────────────┐
│ WAL events    │  `<workspace>/.skills_runtime_sdk/runs/<run_id>/events.jsonl`
└──────────────┘
```

## 安全模型（门卫 vs 围栏）

框架刻意把安全闭环拆成 **两层**（两者缺一不可）：

- **门卫（Policy + Approvals）**：决定“要不要放行执行”。
- **围栏（OS Sandbox）**：决定“放行后最多能执行到什么边界”（OS 级隔离）。

```text
ToolCall
  │
  ├─ Guard（风险识别）            → risk_level + reason（例如 sudo / rm -rf / mkfs）
  │
  ├─ Policy（确定性门卫）         → allow | ask | deny
  │     - 命中 denylist  → deny（不进入 approvals）
  │     - 命中 allowlist → allow（直通，减少打扰）
  │     - require_escalated → ask（即使 mode=allow 也必须询问）
  │
  ├─ Approvals（人类/程序化审批）  → approved | approved_for_session | denied | abort
  │     - 需要 ask 但无 ApprovalProvider → fail-fast（config_error）
  │     - 同一 approval_key 重复 denied → 中止 run（loop guard）
  │
  └─ OS Sandbox（OS 级隔离围栏）   → none | restricted
        - 要求 restricted 但无 adapter → sandbox_denied（不静默降级）
        - macOS：seatbelt（sandbox-exec），Linux：bubblewrap（bwrap）
```

Approvals 的审计键遵循“可审计但不泄密”：
- `approval_key = sha256(canonical_json(tool, sanitized_request))`
- `shell_exec`：记录 `argv` + `env_keys`（不记录 env values）
- `file_write` / `apply_patch`：记录 size + sha256 指纹（不落原文内容）

## 快速开始（5 分钟体验 Studio MVP）

### 0) 前置条件

- Python **3.10+**
- Node.js（仅 Studio 前端需要；建议 **20.19+** 或 **22.12+**）

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

更多契约与多段 namespace 的说明见：`help/13-namespace-mentions.cn.md`。

说明：框架只提取**合法** mention，其它类似片段一律当普通文本处理（不会打断 run）。

---

## 用 `pip` 安装与使用（仅 SDK）

PyPI 包名：`skills-runtime-sdk`（Python `>=3.10`）。

```bash
python -m pip install -U skills-runtime-sdk
```

可选 extras（skills sources）：

```bash
python -m pip install -U "skills-runtime-sdk[redis]"
python -m pip install -U "skills-runtime-sdk[pgsql]"
python -m pip install -U "skills-runtime-sdk[all]"
```

注意：
- Python 的 import 名称是 `skills_runtime`（包名与模块名不同）。
- `pip install` 安装的是 SDK；**Studio MVP 是仓库内的 example**（需要从源码运行）。

### CLI

```bash
skills-runtime-sdk --help
skills-runtime-sdk skills --help
```

### Python 最小示例

```python
# BEGIN README_OFFLINE_MINIMAL
import tempfile
from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall

backend = FakeChatBackend(
    calls=[
        FakeChatCall(
            events=[
                ChatStreamEvent(type="text_delta", text="离线 backend 打招呼"),
                ChatStreamEvent(type="completed", finish_reason="stop"),
            ]
        )
    ]
)

with tempfile.TemporaryDirectory() as d:
    workspace_root = Path(d).resolve()
    agent = Agent(workspace_root=workspace_root, backend=backend)

    result = agent.run("用一句话打个招呼。")
    print("final_output=", result.final_output)
    print("wal_locator=", result.wal_locator)
# END README_OFFLINE_MINIMAL
```

如需接入真实 OpenAI-compatible 后端（base_url/models/overlay 配置），参见 `help/03-sdk-python-api.cn.md` 与 `help/examples/run_agent_minimal.py`。

## Help 导览（建议按顺序）

- 总导航：`help/README.cn.md`
- 20 分钟跑通：`help/01-quickstart.cn.md`
- 配置全集：`help/02-config-reference.cn.md`
- Tools + Safety（approvals + sandbox）：`help/06-tools-and-safety.cn.md`
- Studio 端到端手册：`help/07-studio-guide.cn.md`
- 故障排查：`help/09-troubleshooting.cn.md`

---

## 示例库与编码智能体教学文档

- SDK 全能力离线示例库：`examples/`
- 给编码智能体的教学文档包（CAP 清单 + 覆盖映射 + cheatsheet）：`docs_for_coding_agent/`

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
