# Capability Coverage Map（CAP-* → docs/examples/tests/help/specs）

本表用于把“能力点”与“证据入口”连起来，便于：
- 快速定位实现与契约
- 证明交付质量（tests/worklog/task summary）
- 避免“只完成最小实现就从 backlog 划掉”

> 说明：examples/tests 在本仓库以 **离线可回归** 为默认目标；真模型/联网仅作为可选集成验证。

---

| CAP | Specs（契约） | Help（手册） | Examples（可运行） | Tests（离线回归） |
|---|---|---|---|---|
| CAP-SDK-001 Config | `docs/specs/skills-runtime-sdk/docs/configuration.md` | `help/02-config-reference.cn.md` | `examples/step_by_step/01_offline_minimal_run/` | `packages/skills-runtime-sdk-python/tests/test_config_*.py` |
| CAP-SDK-002 Agent Loop | `docs/specs/skills-runtime-sdk/docs/agent-loop.md` | `help/03-sdk-python-api.cn.md` | `examples/step_by_step/01_offline_minimal_run/` | `packages/skills-runtime-sdk-python/tests/test_agent_*.py` |
| CAP-SDK-003 Tools | `docs/specs/skills-runtime-sdk/docs/tools.md` | `help/06-tools-and-safety.cn.md` | `examples/tools/01_standard_library_read_file/` | `packages/skills-runtime-sdk-python/tests/test_tools_*.py` |
| CAP-SDK-004 Safety | `docs/specs/skills-runtime-sdk/docs/safety.md` | `help/06-tools-and-safety.cn.md` | `examples/step_by_step/03_approvals_and_safety/` | `packages/skills-runtime-sdk-python/tests/test_safety_*.py` |
| CAP-SDK-005 Sandbox | `docs/specs/skills-runtime-sdk/docs/os-sandbox.md` | `help/sandbox-best-practices.cn.md` | `examples/step_by_step/04_sandbox_evidence_and_verification/` + `scripts/integration/os_sandbox_restriction_demo.sh` | `packages/skills-runtime-sdk-python/tests/test_os_sandbox_*.py` |
| CAP-SDK-006 Exec Sessions | `docs/specs/skills-runtime-sdk/docs/tools-exec-sessions.md` | `help/06-tools-and-safety.cn.md` | `examples/step_by_step/05_exec_sessions_across_processes/` | `packages/skills-runtime-sdk-python/tests/test_tools_exec_sessions_*` |
| CAP-SDK-007 Collab | `docs/specs/skills-runtime-sdk/docs/tools-collab.md` | `help/06-tools-and-safety.cn.md` | `examples/step_by_step/06_collab_across_processes/` | `packages/skills-runtime-sdk-python/tests/test_tools_collab_*` |
| CAP-SDK-008 Skills V2 | `docs/specs/skills-runtime-sdk/docs/skills.md` | `help/05-skills-guide.cn.md` | `examples/skills/01_skills_preflight_and_scan/` | `packages/skills-runtime-sdk-python/tests/test_skills_*.py` |
| CAP-SDK-009 Sources | `docs/specs/skills-runtime-sdk/docs/skills-sources-contract.md` | `help/05-skills-guide.cn.md` |（集成脚本）`scripts/integration/skills_sources_docker_no_down.sh` | `packages/skills-runtime-sdk-python/tests/test_skills_sources_*.py` |
| CAP-SDK-010 State/WAL | `docs/specs/skills-runtime-sdk/docs/state.md` | `help/08-architecture-internals.cn.md` | `examples/state/01_wal_replay_and_fork/` | `packages/skills-runtime-sdk-python/tests/test_agent_resume_replay.py` |
| CAP-SDK-012 Studio | `docs/specs/skills-runtime-studio-mvp/SPEC.md` | `help/07-studio-guide.md` | `help/examples/studio-api.http` | `packages/skills-runtime-studio-mvp/**` |
| CAP-SDK-014 Plan + Input | `docs/specs/skills-runtime-sdk/docs/tools-plan-and-input.md` | `help/04-cli-reference.cn.md` | `examples/step_by_step/08_plan_and_user_input/` | `packages/skills-runtime-sdk-python/tests/test_tools_update_plan.py` + `packages/skills-runtime-sdk-python/tests/test_tools_request_user_input.py` |
| CAP-SDK-015 Web/Image | `docs/specs/skills-runtime-sdk/docs/tools-web-and-image.md` | `help/06-tools-and-safety.cn.md` | `examples/tools/02_web_search_disabled_and_fake_provider/` | `packages/skills-runtime-sdk-python/tests/test_tools_web_search.py` |

---

## 例：如何证明“完成了而且完整”

对某个 CAP-*，至少给出：
1. spec（契约）入口
2. examples（可运行证明）
3. tests（离线回归）
4. worklog（命令 + 结果）
5. task summary（结项总结）

---

## Patterns（项目级组合示范 / Workflows）

说明：
- CAP-* 表格强调“能力点”与“证据入口”的映射；
- 当你需要真正落地做项目时，推荐用 `examples/workflows/` 的“项目级组合示范”把多个 CAP 组合成可复刻流水线。

当前 workflows：
- `examples/workflows/01_multi_agent_repo_change_pipeline/`：
  - Skills-First：每个角色能力来自 `skills/*/SKILL.md`，并通过 mention 触发 `skill_injected`
  - Multi-agent：`Coordinator` 同步调度 Analyze/Patch/QA/Report
  - Tools + Safety：apply_patch/shell_exec/file_write 全部走 approvals 证据链
  - 产物：workspace 下生成 `report.md`，包含各子 agent 的 `events_path`
- `examples/workflows/02_single_agent_form_interview/`：
  - Single-agent：request_user_input + update_plan + file_write + shell_exec
  - 证据：human_request/human_response + plan_updated + approval_*
  - 产物：workspace 下生成 `submission.json`
- `examples/workflows/03_multi_agent_reference_driven_pipeline/`：
  - References：skill_ref_read 读取 `references/policy.md` 并驱动后续步骤
  - Tools：apply_patch/shell_exec/file_write
  - 产物：workspace 下生成 `report.md`（含证据指针）
- `examples/workflows/04_map_reduce_parallel_subagents/`：
  - 总分总：Planner → Subagents 并行 → Aggregator（map-reduce 形态）
  - 证据：plan_updated + skill_injected + approval_*（每个子任务独立 WAL）
  - 产物：`subtasks.json` + `outputs/*.md` + `report.md`
- `examples/workflows/05_multi_agent_code_review_fix_qa_report/`：
  - Review→Fix→QA→Report：review 只读边界 + 后续副作用拆分
  - 工具：read_file / apply_patch / shell_exec / file_write（写/执行走 approvals）
- `examples/workflows/06_wal_fork_and_resume_pipeline/`：
  - 断点续做：fork_run + replay resume（run_started.resume.enabled 证据）
  - 产物：`checkpoint.txt` + `final.txt` + `report.md`
- `examples/workflows/07_skill_exec_actions_module/`：
  - actions：frontmatter.actions + skill_exec（默认禁用，显式开启）
  - 产物：`action_artifact.json` + `report.md`
- `examples/workflows/08_studio_sse_integration/`：
  - Studio 集成：API + SSE + approvals（集成示例，需显式 opt-in）
- `examples/workflows/09_branching_router_workflow/`：
  - Router pattern：read_file 输入 → file_write(route.json) → 分支 worker → report 汇总
  - 产物：`task_input.json` + `route.json` + `outputs/*` + `report.md`
- `examples/workflows/10_retry_degrade_workflow/`：
  - Retry + Degrade：attempt（允许失败）→ fallback → report（exit_code + 证据指针）
  - 强调失败可审计：`tool_call_finished.ok == false` 也是证据的一部分
- `examples/workflows/11_collab_parallel_subagents_workflow/`：
  - Collab 原语：master 在 agent loop 内调用 spawn_agent/send_input/wait 管理子 agent
  - 子 agent 仍 Skills-First：独立 WAL + 独立产物（`outputs/*`）
- `examples/workflows/12_exec_sessions_engineering_workflow/`：
  - Exec sessions：在 agent loop 内调用 exec_command/write_stdin 完成交互式工程流
  - 产物：`report.md`（只记录关键标记，避免 PTY 输出噪音）
- `examples/workflows/15_workflow_eval_harness/`：
  - Eval harness：同一 workflow 多次运行 → normalize artifacts → score + diff（Markdown + JSON）
  - 产物：`eval_report.md` + `eval_score.json` + `runs/*`
