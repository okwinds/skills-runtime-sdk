# Runtime Liveness Hardening Apply

## 1) Goal / Scope

- Goal：实现 OpenSpec change `runtime-liveness-hardening`，修复 workspace runtime server 的两类活性硬伤。
- In Scope：
  - 半开 / non-EOF client 不得阻塞后续 `runtime.status`
  - `collab.wait` 与 `collab.send_input` 必须可跨连接并发交互
  - 相关最小离线回归测试、OpenSpec tasks、worklog 留痕
- Out of Scope：
  - `RuntimeClient.ensure_server()` 的保守恢复语义
  - `collab.close` / `collab.resume` 契约漂移
  - Studio MVP / tools CLI / 其它治理问题
- Constraints：
  - 不引入新依赖
  - 保持 Unix socket + secret 鉴权模型
  - 最小范围修改，不触碰 `.gitignore` / `AGENTS.md`

## 2) Context（背景与触发）

- 背景：runtime server 之前采用单线程 `accept + recv + dispatch` 串行模型。
- 触发问题（Symptoms）：
  - 一个连接只要不发 EOF，就会把主循环卡在 `recv()`
  - 一个 client 进入 `collab.wait` 后，第二个 client 的 `collab.send_input` 进不来
- 影响范围（Impact）：
  - runtime 进程“活着但不可服务”
  - persistent collab / runtime.status / shutdown 等本地 RPC 被饥饿

## 3) Spec / Contract（文档契约）

- Contract（接口/事件协议/数据结构）：
  - `openspec/changes/runtime-liveness-hardening/specs/runtime-rpc-liveness/spec.md`
- Acceptance Criteria（验收标准）：
  - 半开连接不再阻塞后续 `runtime.status`
  - stalled request 会在有界时间内被 server 放弃
  - `collab.wait` 缺省等待时，`collab.send_input` 仍可完成，且 wait 最终返回
- Test Plan（测试计划）：
  - RED：两条新活性回归先失败
  - GREEN：同两条回归通过
  - VERIFY：直接相邻的 runtime security/status/cleanup 回归通过
- 风险与降级（Risk/Rollback）：
  - 连接级 worker 引入并发后，对 `ExecSessionManager` 补最小锁保护
  - 若后续发现性能或竞态问题，可回滚到本变更前版本并保留回归测试

## 4) Implementation（实现说明）

### 4.1 Key Decisions（关键决策与 trade-offs）

- Decision：主循环只负责 accept，每个连接交给独立 worker 处理
  - Why：根因是串行 `recv()` / handler 占住主循环
  - Trade-off：引入少量线程，但换来明确的连接隔离
  - Alternatives：整体重写为 asyncio，放弃，变更面过大
- Decision：请求读取增加活性超时
  - Why：避免半开连接无限占用 worker
  - Trade-off：极慢客户端会被主动断开
  - Alternatives：依赖 OS keepalive，放弃，行为不可控
- Decision：`collab.wait` 保持原有缺省等待语义，但内部改为短轮询 join
  - Why：不改用户契约，同时避免 worker 本身完全不可中断
  - Trade-off：多一次轻量轮询
  - Alternatives：把 wait 改成强制快返，放弃，会破坏现有行为

### 4.2 Code Changes（按文件列）

- `packages/skills-runtime-sdk-python/src/skills_runtime/runtime/server.py`：
  - 新增连接级 worker `_serve_connection()`
  - 新增请求读取 helper `_read_request()` 与读超时
  - `serve_forever()` 改为 accept 后立即派发线程
  - `collab.wait` 改为 bounded join 轮询
  - 为 exec 相关路径补 `_exec_lock`
- `packages/skills-runtime-sdk-python/tests/test_runtime_server_security_bounds.py`：
  - 新增半开连接活性回归
  - 断言 stalled 连接最终被 server 放弃
- `packages/skills-runtime-sdk-python/tests/test_runtime_server_crash_restart_semantics.py`：
  - 新增 `collab.wait` + `collab.send_input` 跨连接交互回归
  - 覆盖 `timeout_ms` 缺省场景
- `openspec/changes/runtime-liveness-hardening/tasks.md`：
  - 勾选 8/8 tasks 完成

## 5) Verification（验证与测试结果）

### Unit / Offline Regression（必须）

- 命令：
  - `timeout 120s nice -n 19 ionice -c3 pytest -q packages/skills-runtime-sdk-python/tests/test_runtime_server_security_bounds.py::test_runtime_server_half_open_client_does_not_block_later_status_request packages/skills-runtime-sdk-python/tests/test_runtime_server_crash_restart_semantics.py::test_collab_wait_keeps_send_input_interactive_and_then_returns`
  - `timeout 180s nice -n 19 ionice -c3 pytest -q packages/skills-runtime-sdk-python/tests/test_runtime_server_security_bounds.py packages/skills-runtime-sdk-python/tests/test_runtime_server_crash_restart_semantics.py::test_runtime_status_reports_health_and_counts packages/skills-runtime-sdk-python/tests/test_runtime_server_crash_restart_semantics.py::test_runtime_cleanup_closes_sessions_and_children packages/skills-runtime-sdk-python/tests/test_runtime_server_crash_restart_semantics.py::test_collab_wait_keeps_send_input_interactive_and_then_returns`
- 结果：
  - RED：2 failed，均为第二个请求 socket recv 超时
  - GREEN / VERIFY：目标 7 条 runtime 回归全部通过

### Integration（可选）

- 开关（env）：无
- 命令：未执行
- 结果：无

### Scenario / Regression Guards（强烈建议）

- 新增护栏：
  - 半开连接 + 后续 `runtime.status`
  - 缺省 `collab.wait` + 另一连接 `collab.send_input`
- 防止回归类型：
  - accept loop 被单连接拖死
  - wait 请求饿死后续 RPC

## 6) Results（交付结果）

- 交付物列表：
  - runtime server 活性修复
  - 2 条 runtime liveness regression tests
  - OpenSpec tasks 完成留痕
- 如何使用/如何验收（commands + endpoints + examples）：
  - 直接运行上面的 pytest 命令即可复验本变更

## 7) Known Issues / Follow-ups

- 已知问题：
  - dirty worktree 中已有的 `packages/skills-runtime-sdk-python/src/skills_runtime/runtime/client.py` 让 `test_runtime_restart_runs_orphan_cleanup_and_old_session_not_found` 仍失败；它属于 `ensure_server()` 保守恢复语义问题，不在本 change 范围内。
- 后续建议：
  - 单独起 change 修复 `ensure_server()` 在 crash/restart 后的 stale cleanup / restart 语义
  - 单独修复 `collab.close` / `collab.resume` 与 spec 的语义漂移

## 8) Doc Index Update

- 已在 `DOCS_INDEX.md` 登记：是
