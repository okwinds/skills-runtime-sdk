# Cheatsheet（中文）

> 目的：给编码智能体一个“可执行的最短路径”与关键入口索引。

## 0) 最小共识（不要绕过）

- **文档优先**：变更前先写/更新 spec（见 `AGENTS.md`）。
- **TDD 门禁**：功能完成 = 离线回归通过（至少 `bash scripts/pytest.sh`）。
- **可复现**：不提交 secrets；示例命令必须可跑通。

## 1) 快速定位入口

- 全仓库文档索引：`DOCS_INDEX.md`
- 未尽事宜（future / done memo）：`docs/backlog.md`
- 工作记录（命令 + 结果）：`docs/worklog.md`
- 规格入口（SDK）：`docs/specs/skills-runtime-sdk/README.md`
- 接入/运维手册（Help）：`help/README.cn.md`

## 2) 最短跑通（离线）

1) 跑全量离线回归（最推荐）：

```bash
bash scripts/pytest.sh
```

2) 只跑示例 smoke tests：

```bash
pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py
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
  python3 -m agent_sdk.cli.main skills preflight --workspace-root . --config help/examples/skills.cli.overlay.yaml --pretty

PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 -m agent_sdk.cli.main skills scan --workspace-root . --config help/examples/skills.cli.overlay.yaml --pretty
```

Tools CLI（用于验证 tools registry/exec sessions/collab/web_search 等）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 -m agent_sdk.cli.main tools list --pretty
```

## 5) 沙箱 / Docker（关键结论）

- 入口文档：`help/sandbox-best-practices.cn.md`
- Docker（Debian 13 / Ubuntu 20.04/24.04）结论：
  - **macOS seatbelt（sandbox-exec）在 Linux 容器里不可用**
  - **Linux bubblewrap（bwrap）在容器里“有条件可用”**（取决于宿主内核与容器 seccomp/apparmor/caps）
  - 一键探测脚本：`bash scripts/integration/os_sandbox_bubblewrap_probe_docker.sh`

