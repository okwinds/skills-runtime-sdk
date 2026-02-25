# Test Cases: BL-004/005/006/007 交付完整性复核

## Overview

- **Feature**：runtime 跨进程持久化（exec/collab）+ CLI UTF-8 启动兜底 + WAL replay/fork（Phase 4）
- **Requirements Source**：
  - `docs/task-summaries/2026-02-13-bl-004-007-runtime-persistence-and-state-replay.md`
  - `docs/specs/skills-runtime-sdk/docs/tools-exec-sessions.md`
  - `docs/specs/skills-runtime-sdk/docs/tools-collab.md`
  - `docs/specs/skills-runtime-sdk/docs/state.md`
- **Test Coverage**：
  - 自动化回归（pytest）：覆盖核心 happy path + 关键错误路径 + replay/approvals cache
  - 手工检查：runtime 安全边界（socket 权限/secret 不泄露）与运行产物位置
- **Last Updated**：2026-02-13

---

## Requirements（按能力拆分）

### BL-004：exec sessions 跨进程持久化

- **REQ-004-01**：`exec_command` 返回的 `session_id` 可在不同 CLI 进程中继续 `write_stdin` 生效（runtime server 生命周期内）。
- **REQ-004-02**：未知 `session_id` 时应返回 not_found（而不是静默成功或崩溃）。
- **REQ-004-03**：支持显式回收（close/close_all，best-effort），避免长驻子进程泄露。

### BL-005：collab primitives 跨进程持久化

- **REQ-005-01**：`spawn_agent` 返回 child id；`send_input` + `wait` 可跨进程复用同一 id。
- **REQ-005-02**：`close_agent` 可跨进程取消 child，并在 `wait` 中可观测为 `cancelled`。

### BL-006：CLI UTF-8 启动健壮性

- **REQ-006-01**：在 `LANG/LC_ALL=C` + `PYTHONIOENCODING=ascii` 的环境下执行 `--help` 不崩溃，且 exit code=0。

### BL-007：Phase 4 WAL replay + fork

- **REQ-007-01**：`run.resume_strategy=replay` 时：从 WAL 重建 history（tool outputs + assistant final output），且不再注入 `[Resume Summary]`。
- **REQ-007-02**：`replay` 模式尽量恢复 approvals cache（`approved_for_session`），避免重启后重复 ask。
- **REQ-007-03**：fork 能从 WAL 指定事件点分叉 run（截取 events.jsonl 前缀并重写 run_id），fork 后 replay 仍能看到 tool message。

---

## Test Case Categories

### 1. Functional Tests（核心功能）

#### TC-F-004-001: exec sessions 跨进程 write_stdin 生效
- **Requirement**：REQ-004-01
- **Priority**：High
- **Preconditions**：
  - 可运行 Python
  - tools CLI 可调用（`python -m skills_runtime.cli.main tools ...`）
- **Test Steps**：
  1. 进程 A 调用 `tools exec-command` 启动交互式 session（REPL 模式：readline）。
  2. 进程 B 调用 `tools write-stdin` 写入 `hello\\r` 并轮询输出直到退出。
- **Expected Results**：
  - 输出包含 `got:hello`
  - session_id 在跨进程调用中可复用
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_tools_exec_sessions_cli_persistence.py::test_exec_command_write_stdin_across_processes`

#### TC-F-004-002: exec sessions 显式 close 回收资源
- **Requirement**：REQ-004-03
- **Priority**：High
- **Preconditions**：
  - runtime server 可被自动拉起（workspace 内）
- **Test Steps**：
  1. 创建 `PersistentExecSessionManager`，spawn 一个长睡眠进程（保持 running）。
  2. 调用 `close(session_id)`。
  3. 再次 `write(session_id)`。
- **Expected Results**：
  - close 后 write 抛 `KeyError`（not_found 语义）
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_persistent_exec_sessions_close.py::test_persistent_exec_sessions_close_terminates_session`

#### TC-F-005-001: collab spawn/send/wait 跨进程复用 child id
- **Requirement**：REQ-005-01
- **Priority**：High
- **Test Steps**：
  1. 进程 A：`tools spawn-agent`（message=`wait_input:*`）获取 child id。
  2. 进程 B：`tools send-input` 投递 `ping`。
  3. 进程 C：`tools wait` 等待完成。
- **Expected Results**：
  - wait 返回的结果中该 id 状态为 completed（并输出 `got:ping`）或仍为 running（随后完成）
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_tools_collab_cli_persistence.py::test_spawn_send_wait_across_processes`

#### TC-F-005-002: collab close 可跨进程取消并可观测
- **Requirement**：REQ-005-02
- **Priority**：High
- **Test Steps**：
  1. 进程 A：`tools spawn-agent`（message=`wait_input:*`）。
  2. 进程 B：`tools close-agent` 取消该 child。
  3. 进程 C：`tools wait` 等待/查询状态。
- **Expected Results**：
  - wait 结果中该 id 的 `status == cancelled`
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_tools_collab_cli_persistence.py::test_spawn_close_wait_across_processes`

#### TC-F-006-001: CLI 在 C locale 下 `--help` 不崩溃
- **Requirement**：REQ-006-01
- **Priority**：High
- **Test Steps**：
  1. 设置环境变量：`LANG=C`、`LC_ALL=C`、`PYTHONIOENCODING=ascii`。
  2. 执行：`python -m skills_runtime.cli.main --help`。
- **Expected Results**：
  - 进程返回码为 0
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_cli_utf8_startup_c_locale.py::test_cli_help_does_not_crash_in_c_locale`

#### TC-F-007-001: replay resume 重建 tool message 且不注入 summary
- **Requirement**：REQ-007-01
- **Priority**：High
- **Test Steps**：
  1. 第一次 run：产生 WAL（包含 tool_call_finished + run_completed）。
  2. 第二次 run（同 run_id + replay）：断言 messages 中存在 tool role message，且不含 `[Resume Summary]`。
- **Expected Results**：
  - tool message 存在
  - 未注入 summary
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_agent_resume_replay.py::test_agent_resume_replay_reconstructs_history_from_wal`

#### TC-F-007-002: replay resume 恢复 approvals cache（approved_for_session）
- **Requirement**：REQ-007-02
- **Priority**：High
- **Test Steps**：
  1. 第一次 run：ApprovalProvider 返回 `APPROVED_FOR_SESSION`，并执行一次 `file_write`。
  2. 第二次 run（同 run_id + replay）：ApprovalProvider 若被调用会直接报错；触发相同 request 的 `file_write`。
- **Expected Results**：
  - 第二次 run 不调用 provider（命中 cached）
  - WAL 中可观测 `approval_decided.reason == "cached"`
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_agent_resume_replay.py::test_agent_resume_replay_restores_approved_for_session_cache`

#### TC-F-007-003: fork 后 replay 仍能看到 tool message
- **Requirement**：REQ-007-03
- **Priority**：High
- **Test Steps**：
  1. 源 run 产生 WAL。
  2. 找到 `tool_call_finished` 行号并 fork 到新 run_id。
  3. fork run 使用 replay resume 继续执行。
- **Expected Results**：
  - fork run 的 replay history 包含 tool role message
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_state_fork_run_replay.py::test_fork_run_then_replay_resume`

---

### 2. Error Handling Tests（错误路径）

#### TC-ERR-004-001: write_stdin unknown session_id 返回 not_found
- **Requirement**：REQ-004-02
- **Priority**：High
- **Test Steps**：
  1. 调用 `write_stdin(session_id=999)`。
- **Expected Results**：
  - `error_kind == "not_found"`
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_tools_exec_sessions.py::test_write_stdin_not_found`

---

### 3. State Transition Tests（状态变化）

#### TC-ST-005-001: collab 状态从 running → cancelled
- **Requirement**：REQ-005-02
- **Priority**：Medium
- **Test Steps**：
  1. spawn `wait_input:*`（running）。
  2. close-agent（cancelled）。
  3. wait 查询。
- **Expected Results**：
  - 状态转移可观测为 `cancelled`
- **Automation**：同 `TC-F-005-002`

---

## Test Coverage Matrix

| Requirement ID | Test Cases | Coverage Status |
|---|---|---|
| REQ-004-01 | TC-F-004-001 | ✓ Complete |
| REQ-004-02 | TC-ERR-004-001 | ✓ Complete |
| REQ-004-03 | TC-F-004-002 | ✓ Complete |
| REQ-005-01 | TC-F-005-001 | ✓ Complete |
| REQ-005-02 | TC-F-005-002, TC-ST-005-001 | ✓ Complete |
| REQ-006-01 | TC-F-006-001 | ✓ Complete |
| REQ-007-01 | TC-F-007-001 | ✓ Complete |
| REQ-007-02 | TC-F-007-002 | ✓ Complete |
| REQ-007-03 | TC-F-007-003 | ✓ Complete |

---

## Notes

- runtime server 异常退出后，历史 exec sessions 不保证可恢复（最小语义：not_found），该取舍已在 `docs/specs/skills-runtime-sdk/docs/tools-exec-sessions.md` 明确。
- runtime 安全边界（手工检查建议）：
  - 确认 socket 权限为 0600（仅当前用户访问）；
  - 确认 `server.json` 中 secret 仅用于本机鉴权，不打印到日志/不进入事件/WAL。

