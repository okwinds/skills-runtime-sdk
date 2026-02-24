# Recipes（怎么把 SDK 用到具体场景里）

本页目标：把“能力”变成可复制的“落地形态”。每条配方都满足：
- 讲清楚适用场景与边界
- 指向一个可运行 examples（离线可回归）
- 给出最小验收口径（tests / evidence）

---

## 配方 1：安全的 Repo 修改助手（推荐起步）

适用：
- 你要做“读代码→提出修改→落 patch→可回放/可审计”的助手

关键能力组合：
- Tools 标准库：`read_file/grep_files/apply_patch`
- Safety + approvals：写操作必须可审计
- State/WAL：出问题能 replay/fork 复盘

对应示例：
- `examples/step_by_step/02_offline_tool_call_read_file/`
- `examples/tools/01_standard_library_read_file/`
- `examples/state/01_wal_replay_and_fork/`

验收建议：
- 离线门禁：`bash scripts/pytest.sh`
- evidence：`events.jsonl` 中有 `tool_call_*` 与（如适用）`approval_*`

---

## 配方 2：受控命令执行器（Approvals + Sandbox）

适用：
- 你需要让 agent 执行本地命令，但必须有“门卫 + 围栏 + 证据”

关键能力组合：
- `shell_exec`（argv）
- approvals：`safety.mode=ask`
- sandbox：推荐 `sandbox.profile=balanced`（分阶段收紧的高层宏；也可直接用 `sandbox.default_policy=restricted`）
- evidence：`data.sandbox.*`

对应示例：
- `examples/step_by_step/03_approvals_and_safety/`
- `examples/step_by_step/04_sandbox_evidence_and_verification/`

验收建议：
- 离线：能稳定产出 `data.sandbox` meta；restricted 且无 adapter 时必须 fail-closed（sandbox_denied）
- 真沙箱（可选）：运行 `scripts/integration/os_sandbox_restriction_demo.sh` 并检查 evidence 字段变化

---

## 配方 3：工具型“长会话”体验（Exec Sessions + Collab）

适用：
- 你要在 CLI/服务端做一个“可持续交互”的会话（PTY）；
- 或者需要把子 agent 当作协程/子进程去编排

关键能力组合：
- exec sessions：`exec_command` + `write_stdin`
- collab primitives：`spawn_agent` + `send_input` + `wait` + `close_agent`
- 跨进程可复用（runtime server 生命周期内）

对应示例：
- `examples/step_by_step/05_exec_sessions_across_processes/`
- `examples/step_by_step/06_collab_across_processes/`

验收建议：
- 多次 CLI 调用复用同一 `session_id` / `child id`
- wait 可观测状态：`completed/running/failed/cancelled`

---

## 配方 4：Skills 驱动的业务助手（References + Actions）

适用：
- 你要把可复用能力打包成 Skills（SKILL.md），并允许：
  - 引用材料读取（references）
  - 受控动作执行（actions）

关键能力组合：
- skills preflight/scan/mentions（explicit spaces/sources）
- `skill_ref_read`（fail-closed，默认禁用）
- `skill_exec`（走 `shell_exec` 等价的 approvals/sandbox gate）

对应示例：
- `examples/skills/01_skills_preflight_and_scan/`
- `examples/step_by_step/07_skills_references_and_actions/`
- `examples/workflows/20_policy_compliance_patch/`（policy 合规补丁：references/policy.md → apply_patch → artifacts）

验收建议：
- references/actions 默认禁用（permission）
- 启用后：ref_read 只能读 references（可选 assets）；actions argv 必须在 bundle/actions 内

---

## 配方 5：需求澄清与任务同步（Plan + request_user_input）

适用：
- 你要把任务推进过程“结构化可见”（plan），并且在关键决策点向人类请求结构化输入（避免长对话歧义）

关键能力组合：
- `update_plan`：计划变更可审计（有 WAL 时为 `plan_updated`）
- `request_user_input`：结构化提问（无 human_io 时必须 fail-closed）

对应示例：
- `examples/step_by_step/08_plan_and_user_input/`
- `examples/workflows/22_chatops_incident_triage/`（ChatOps 排障：澄清→计划→runbook/report）

验收建议：
- 离线：通过 tools CLI 的 `--answers-json` 可注入答案，避免示例阻塞
- 集成：在真实 UI/CLI 中接入 HumanIOProvider，并观察 `human_request/human_response` 事件

---

## 配方 6：受控联网检索（web_search：默认关闭 + 显式注入 provider）

适用：
- 你需要让 agent 进行联网检索，但必须满足：
  - 明确的启用开关（默认关闭）
  - 有治理（限流/配额/审计/脱敏）
  - 离线回归仍可跑通（fake provider）

关键能力组合：
- `web_search`：工具契约（结构化 results）
- provider 注入：产品侧显式注入 `ctx.web_search_provider`

对应示例：
- `examples/tools/02_web_search_disabled_and_fake_provider/`

验收建议：
- 默认关闭时：返回 `error_kind=validation` 且 `data.disabled=true`
- 启用 provider 后：离线可回归地返回结构化 `results[]`

---

## 配方 7：Skills-First 多 Agent 项目流水线（Workflows / Project-level）

适用：
- 你希望把 SDK 真的“用起来做项目”，而不是只演示单个 CAP 原语；
- 你希望把每个角色能力沉淀为 Skills（`SKILL.md`），编排层只负责组合与证据链。

核心原则（必须）：
- **Skills 是最小单元**：Analyze/Patch/QA/Report 等角色能力必须以独立 Skill 表达；
- 每个子任务必须包含对应 skill mention（例如 `$[examples:workflow].repo_patcher`），以触发注入与 `skill_injected` 证据事件；
- 副作用（写文件/打补丁/跑命令）仍通过 builtin tools 执行，以保留 approvals/sandbox/WAL 的审计链路。

关键能力组合：
- Skills V2：spaces/sources 扫描与 mention 注入（`skill_injected`）
- Multi-agent：`Coordinator`（同步 child → summary → 汇总）
- Tools：`read_file` + `apply_patch` + `shell_exec` + `file_write`
- Safety + approvals：`safety.mode=ask`（离线示例用 scripted provider 自动批准）
- State/WAL：每个 agent 都落 `events.jsonl`，可用于回放与排障

对应示例：
- `examples/workflows/01_multi_agent_repo_change_pipeline/`
- `examples/workflows/03_multi_agent_reference_driven_pipeline/`（references 驱动：skill_ref_read 读取 policy）
- `examples/workflows/18_fastapi_sse_gateway_minimal/`（本地 FastAPI + SSE + approvals decide：网关骨架）
- `examples/workflows/20_policy_compliance_patch/`（policy 合规补丁：references → patch → artifacts）
- `examples/workflows/22_chatops_incident_triage/`（ChatOps 排障：human I/O → plan → runbook/report）

验收建议：
1. 离线门禁：
   - `pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py`
2. 证据链：
   - 每个子 agent 的 WAL 中至少出现 1 条 `skill_injected`（payload.mention_text 为对应 mention）
   - Patch/QA/Report 的 WAL 中出现 `approval_requested/approval_decided`
   - `tool_call_finished.result.ok == true`（apply_patch/shell_exec/file_write）
3. 产物：
   - workspace 下生成 `report.md`（包含各步骤摘要 + events_path 指针）

---

## 配方 8：单 Agent 多轮表单访谈（Human I/O + Plan + 产物落盘）

适用：
- 你要做一个“真实业务常见形态”的工作流：多轮收集结构化信息（表单/工单/登记），并提供可审计证据链。

关键能力组合：
- `request_user_input`：结构化 human I/O（无 human_io provider 时 fail-closed）
- `update_plan`：结构化进度同步（`plan_updated` 事件）
- `file_write`：落盘产物（例如 `submission.json`）
- `shell_exec`：最小确定性校验（例如解析 JSON 并断言）
- Skills-First：访谈/校验/落盘能力来自 Skills（mentions 触发 `skill_injected`）

对应示例：
- `examples/workflows/02_single_agent_form_interview/`

验收建议：
- 离线门禁：`pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py`
- 证据链：WAL 中应出现 `human_request/human_response`、`plan_updated`、`approval_*`
- 产物：workspace 下生成 `submission.json`

---

## 配方 9：总分总 + 并行子任务（Map-Reduce 编排）

适用：
- 你要做“总任务拆解 → 并行子任务 → 汇总报告”的项目级编排；
- 子任务相互独立（互不影响），但最终需要聚合为一份结论/报告。

关键能力组合：
- Planner：`update_plan` + `file_write(subtasks.json)`（结构化拆解 + 可审计产物）
- Subagents：并行执行（每个子任务一个 Skill），各写独立产物（例如 `outputs/*.md`）
- Aggregator：汇总产物 + events_path 指针，生成 `report.md`
- Skills-First：Planner/Subagent/Aggregator 全部通过 mentions 注入并产生 `skill_injected` 证据事件

对应示例：
- `examples/workflows/04_map_reduce_parallel_subagents/`

验收建议：
- 子任务产物互不覆盖（路径隔离）
- `report.md` 中列出每个子任务的 `events_path`

---

## 配方 10：Review→Fix→QA→Report（把 code review 变成流水线）

适用：
- 你希望把“评审只读边界”与“修复/执行/落盘副作用”拆开，形成可审计流水线。

关键能力组合：
- Reviewer：只读（read_file/grep_files），输出问题与修复建议
- Fixer：`apply_patch` 落最小补丁（approvals）
- QA：`shell_exec` 跑最小断言（approvals）
- Reporter：`file_write` 输出报告（approvals）

对应示例：
- `examples/workflows/05_multi_agent_code_review_fix_qa_report/`

---

## 配方 11：断点续做（WAL fork + replay resume）

适用：
- 长任务中途失败/进程重启后，希望从“已完成断点”继续跑，而不是从头再来。

关键能力组合：
- 断点产物（checkpoint）：`file_write` 写一个稳定产物（例如 `checkpoint.txt`）
- Fork 点选择：读取 WAL（events.jsonl）定位最近一次关键步骤成功
- `fork_run(...)`：生成新 run_id 的 WAL 前缀
- replay resume：`run.resume_strategy=replay`，尽量恢复 tool outputs 与 approvals cache

对应示例：
- `examples/workflows/06_wal_fork_and_resume_pipeline/`

验收建议：
- 第二次 run 的 `run_started.payload.resume.enabled == true` 且 `strategy == replay`
- 最终产物（例如 `final.txt`）存在

---

## 配方 12：Skill Actions（skill_exec）

适用：
- 你需要把“可执行动作”随 Skill 一起分发（例如生成文件、跑脚本），并保持审批/沙箱/证据链。

关键能力组合：
- `skills.actions.enabled=true`（默认禁用）
- `actions`：在 SKILL.md frontmatter 声明 action（argv/timeout/env）
- `skill_exec`：受控执行 action（仍走 approvals/sandbox gate）

对应示例：
- `examples/workflows/07_skill_exec_actions_module/`

---

## 配方 13：Studio 集成（API + SSE + Approvals）

适用：
- 你希望把能力做成“产品形态”：后端 run + SSE 事件流 + 前端 UI；
- 或你要接入自己的客户端（脚本/服务）来消费 SSE 并处理 approvals。

关键能力组合：
- Studio endpoints：create session/run + events stream
- SSE：按 `event/data` 解析并监听 terminal event
- Approvals：监听 `approval_requested` 并调用 decide API

对应示例（集成，需显式 opt-in）：
- `examples/workflows/08_studio_sse_integration/`

---

## 配方 14：路由分支（Router pattern）

适用：
- 你希望一个入口任务按条件走不同分支（A/B/...），但最终汇总成同一份报告；
- 你希望“分支决策”可审计、可回放（而不是埋在 prompt 里）。

关键能力组合：
- Router：`read_file` 读取输入 → `file_write(route.json)` 落盘决策
- Branch worker：每个分支一个 Skill，各写独立产物（例如 `outputs/path_a.md`）
- Reporter：`file_write(report.md)` 汇总（包含 `events_path` 指针）

对应示例：
- `examples/workflows/09_branching_router_workflow/`

验收建议：
- `route.json` 存在且结构稳定（后续可被别的工具/流程消费）
- `report.md` 能指向路由/分支产物及证据路径

---

## 配方 15：重试→降级→报告（Retry + Degrade + Report）

适用：
- 真实项目中，外部依赖/工具执行可能失败；
- 你需要“失败也可审计”，并在预算耗尽后走降级路径（fallback）。

关键能力组合：
- Controller：`update_plan` + `file_write(retry_plan.json)`（预算/策略可审计）
- Attempt：`shell_exec`（允许失败；exit_code/ok 作为证据）
- Degrade：`file_write(outputs/fallback.md)`（生成最小可用结果）
- Reporter：`file_write(report.md)`（汇总每次 attempt 的 exit_code + events_path）

对应示例：
- `examples/workflows/10_retry_degrade_workflow/`

验收建议：
- attempt 的 `tool_call_finished.ok == false` 也必须存在（失败仍可审计）
- 报告中体现：预算、每次 attempt 的 exit_code、降级结论与产物路径

---

## 配方 16：Collab 原语并行子 agent（spawn/wait/send_input）

适用：
- 子任务数量动态变化（不希望编排层自己维护线程池）
- 希望在执行中途给子 agent 追加输入（例如补充约束/数据/反馈）

关键能力组合：
- master：`spawn_agent` / `send_input` / `wait`
- subagents：仍然 Skills-First（各自独立 WAL + 独立产物）
- aggregator：汇总输出（例如 `report.md`）

对应示例：
- `examples/workflows/11_collab_parallel_subagents_workflow/`

验收建议：
- master 的 WAL 中应出现 `tool_call_finished`（spawn_agent/send_input/wait）
- 子 agent 的 WAL 中必须出现 `skill_injected`（证明能力来自 SKILL.md）

---

## 配方 17：exec sessions 工程式交互（exec_command/write_stdin）

适用：
- 需要实现交互式工程流（REPL/交互脚本/生成器 CLI）
- 希望把交互过程纳入 approvals + WAL 的可审计证据链

关键能力组合：
- `exec_command`：启动 PTY-backed 会话
- `write_stdin`：写入输入并轮询输出
- Reporter：只记录关键标记是否出现，避免 PTY 输出细节导致回归不稳定

对应示例：
- `examples/workflows/12_exec_sessions_engineering_workflow/`

验收建议：
- WAL 中应出现 `exec_command/write_stdin` 的 `tool_call_finished`
- `report.md` 只写关键标记（READY/ECHO/BYE 等）

---

## 配方 18：workflow eval harness（多次运行对比 artifacts）

适用：
- 你希望把 workflow 当作“可评测对象”，建立回归护栏（类似 eval harness）
- 需要自动化对比 artifacts，并输出 score + diff 摘要

关键能力组合：
- 多次运行同一 workflow
- 收集 artifacts（例如 `report.md`、`outputs/*`）
- normalize（去掉 workspace 绝对路径、WAL run_id 等噪音）
- 输出 `eval_score.json` + `eval_report.md`

对应示例：
- `examples/workflows/15_workflow_eval_harness/`

验收建议：
- `eval_score.json` 可被 CI 消费（结构化）
- diff 摘要能快速定位不一致 artifact

---

## 附：更多“具体落地场景”示例（Workflows / 离线可回归）

这些示例更偏“项目形态”，通常同时覆盖：skills-first（mention → `skill_injected`）、approvals、WAL 证据链与产物落盘。

- 规则驱动的结构化解析器：`examples/workflows/16_rules_based_parser/`
  - 自然语言规则 → `plan.json` → 确定性执行 `result.json` → `report.md`
- 最小 RAG（离线 stub）：`examples/workflows/17_minimal_rag_stub/`
  - 自定义 `kb_search`（关键词检索）→ `retrieval.json` → `report.md`
- 离线 view_image：`examples/workflows/19_view_image_offline/`
  - 生成 PNG → `view_image` → `image_meta.json` / `report.md`
- 数据导入校验与修复：`examples/workflows/21_data_import_validate_and_fix/`
  - `read_file(input.csv)` → `file_write(fixed.csv/report)` → `shell_exec(QA_OK)` → `report.md`
