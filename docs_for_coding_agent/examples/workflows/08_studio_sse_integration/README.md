# 08_studio_sse_integration（Studio API + SSE 端到端 / 集成示例）

本示例演示如何用“脚本客户端”的方式走一遍 Studio MVP 的端到端链路：

- 创建 session（并设置 skills roots）
- 创建 run（message 含 skill mention）
- 订阅 SSE：`/api/v1/runs/<run_id>/events/stream`
- 自动处理 approvals（看到 `approval_requested` 就调用 decide API）
- 等待 `run_completed/run_failed/run_cancelled` 终止事件

重要说明：
- 这是 **集成示例**：需要你先启动 Studio backend，并配置可用的 LLM（`OPENAI_API_KEY` 或本地 base_url）。
- 默认 **不进入离线门禁**（不会被 `test_examples_smoke.py` 调用）。

## 如何运行（需要显式 opt-in）

1) 启动 Studio backend：

```bash
bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

2) 运行本示例（必须显式开启）：

```bash
export SKILLS_RUNTIME_SDK_RUN_INTEGRATION=1
export SKILLS_RUNTIME_STUDIO_BASE_URL="http://127.0.0.1:8000"

python3 docs_for_coding_agent/examples/workflows/08_studio_sse_integration/run.py
```

## 你将看到

- 控制台输出：run_id、关键 SSE 事件、最终状态
- 若 LLM 按 skill 指示触发写操作，将出现 `approval_requested`（脚本会自动批准）
