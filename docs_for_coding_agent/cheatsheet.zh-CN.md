# Cheatsheet（中文）

> 目的：给编码智能体一个“可执行的最短路径”与关键入口索引。

## 0) 最小共识（不要绕过）

- **文档优先**：变更前先写清楚 `Goal/AC/Test Plan`（写在 PR/issue 或你们内部文档里即可；OSS 版本不依赖内部协作文档）。
- **TDD 门禁**：功能完成 = 离线回归通过（至少 `bash scripts/pytest.sh`）。
- **可复现**：不提交 secrets；示例命令必须可跑通。

## 1) 快速定位入口

- 接入/运维手册（Help）：`help/README.cn.md`
- 编码智能体教学索引：`docs_for_coding_agent/DOCS_INDEX.md`
- 示例库（离线可回归，教学/能力覆盖）：`docs_for_coding_agent/examples/`
- 示例库（面向人类的应用示例）：`examples/apps/`

## 1.1) 内部架构（M2 已完成）

对外入口是 `Agent`（薄门面，`core/agent.py`）。内部组件边界：

- `AgentLoop`（`core/agent_loop.py`）：turn 循环、LLM 调用、工具分发。
- `SafetyGate`（`safety/gate.py`）：统一安全门禁，替代原 if/elif 分发链。
- `ToolSafetyDescriptor`（`tools/protocol.py`）：工具自描述安全属性的 Protocol。

示例与外部集成只使用公开 API（`Agent`、`tools/protocol.py`、`safety/approvals.py`、`llm/`），不直接导入 `core/agent_loop.py` 或 `safety/gate.py`。

## 2) 最短跑通（离线）

1) 跑全量离线回归（最推荐）：

```bash
bash scripts/pytest.sh
```

2) 只跑示例 smoke tests：

```bash
pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py
```

3) CI 同款最小门禁（包含 repo+SDK+Studio 的离线回归）：

```bash
bash scripts/tier0.sh
```

## 3) 最短跑通（真模型，可选）

> 仅在你确实有 API key、且任务需要联网/真模型时才做。

```bash
cp help/examples/sdk.overlay.yaml /tmp/sdk.overlay.yaml
export OPENAI_API_KEY='...'
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 help/examples/run_agent_minimal.py --workspace-root . --config /tmp/sdk.overlay.yaml
```

## 4) 常用 CLI（skills/tools）

Skills 预检与扫描（确认 spaces/sources 配置是否正确）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 -m skills_runtime.cli.main skills preflight --workspace-root . --config help/examples/skills.cli.overlay.yaml --pretty

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 -m skills_runtime.cli.main skills scan --workspace-root . --config help/examples/skills.cli.overlay.yaml --pretty
```

Tools CLI（用于验证 tools registry/exec sessions/collab/web_search 等）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 -m skills_runtime.cli.main tools list-dir --workspace-root . --dir-path . --pretty
```

## 5) 沙箱 / Docker（关键结论）

- 入口文档：`help/sandbox-best-practices.cn.md`
- Docker（Debian 13 / Ubuntu 20.04/24.04）结论：
  - **macOS seatbelt（sandbox-exec）在 Linux 容器里不可用**
  - **Linux bubblewrap（bwrap）在容器里“有条件可用”**（取决于宿主内核与容器 seccomp/apparmor/caps）
  - 一键探测脚本：`bash scripts/integration/os_sandbox_bubblewrap_probe_docker.sh`

## 6) 内部生产（可选）：强制本地协作文件校验

> OSS 版本默认不强制要求“本地协作文件”存在；内部生产如需强制，可显式开启：

```bash
REQUIRE_LOCAL_DOCS=1 bash scripts/tier0.sh
```

说明与约束见：`help/12-validation-suites.cn.md`（12.2.1）。
