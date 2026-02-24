# Quickstart（离线：最短跑通）

目标：在 **不需要真实模型/外网** 的情况下，跑通 SDK 的核心链路，并且知道“证据与产物”在哪里。

> 说明：本 quickstart 以 `examples/step_by_step/*` 为准；Help 的真模型示例属于可选集成验证。

重要前置：
- Python 版本要求：SDK 代码默认使用 `str | None` 等 typing 语法，要求 **Python >= 3.10**。
- 在 macOS 上，系统自带的 `/usr/bin/python3` 可能仍是 3.9；请优先使用你项目的虚拟环境（例如 conda/venv）对应的 `python`。

---

## 1) 一条命令跑离线门禁

```bash
bash scripts/pytest.sh
```

这会跑：
- root 级少量测试
- SDK 全量离线回归（pytest）

---

## 2) 只跑示例 smoke tests（更快）

```bash
pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py
```

---

## 3) 逐个跑 step_by_step（理解核心闭环）

建议按顺序：
- `examples/step_by_step/01_offline_minimal_run/`：离线最小 run（FakeChatBackend）+ wal_locator
- `examples/step_by_step/02_offline_tool_call_read_file/`：tool_calls → 工具执行 → 回注 → 完成
- `examples/step_by_step/03_approvals_and_safety/`：审批 ask/deny + approved_for_session 缓存
- `examples/step_by_step/04_sandbox_evidence_and_verification/`：`data.sandbox` 证据字段 + 真实沙箱验证入口
- `examples/step_by_step/05_exec_sessions_across_processes/`：exec sessions 跨进程复用（tools CLI）
- `examples/step_by_step/06_collab_across_processes/`：collab primitives 跨进程复用（tools CLI）
- `examples/step_by_step/07_skills_references_and_actions/`：skills ref_read + actions（skill_exec + 审批）
- `examples/step_by_step/08_plan_and_user_input/`：计划与结构化人机输入（update_plan + request_user_input）

运行方式（示例）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/step_by_step/03_approvals_and_safety/run.py --workspace-root /tmp/srsdk-demo
```

---

## 4) 你应该观察什么（证据链）

1) `wal_locator`（WAL locator）：
- 默认 file WAL 时，它通常是路径：`<workspace_root>/.skills_runtime_sdk/runs/<run_id>/events.jsonl`
- 若注入了非文件型 WAL（`WalBackend`），它也可能是 `wal://...` 形式的定位符；终态事件 payload 会同时提供 `wal_locator`（推荐字段）

2) approvals 证据：
- 事件：`approval_requested` / `approval_decided`
- tool deny 时：`tool_call_finished.result.error_kind == "permission"`

3) sandbox 证据（不要凭体感）：
- 字段：`tool_call_finished.result.data.sandbox.{requested,effective,adapter,active}`
