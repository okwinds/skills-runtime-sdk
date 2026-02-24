# Capability Coverage Map（CAP-* → help/examples/tests/code）

本表用于把“能力点”与“证据入口”连起来，便于：
- 快速定位实现与契约
- 证明交付质量（tests + examples + 可观测 evidence 字段）
- 避免“只完成最小实现就从 backlog 划掉”

> 说明：examples/tests 在本仓库以 **离线可回归** 为默认目标；真模型/联网仅作为可选集成验证。

---

| CAP | Help（契约/用法） | Code（实现入口） | Examples（可运行） | Tests（离线回归） |
|---|---|---|---|---|
| CAP-SDK-001 Config | `help/02-config-reference.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/config/loader.py` | `examples/step_by_step/01_offline_minimal_run/` | `packages/skills-runtime-sdk-python/tests/test_config_*.py` |
| CAP-SDK-002 Agent Loop | `help/03-sdk-python-api.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/core/agent.py` | `examples/step_by_step/01_offline_minimal_run/` | `packages/skills-runtime-sdk-python/tests/test_agent_*.py` |
| CAP-SDK-003 Tools | `help/06-tools-and-safety.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/tools/registry.py` | `examples/tools/01_standard_library_read_file/` | `packages/skills-runtime-sdk-python/tests/test_tools_*.py` |
| CAP-SDK-004 Safety | `help/06-tools-and-safety.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/safety/*` | `examples/step_by_step/03_approvals_and_safety/` | `packages/skills-runtime-sdk-python/tests/test_safety_*.py` |
| CAP-SDK-005 Sandbox | `help/sandbox-best-practices.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/sandbox/*` | `examples/step_by_step/04_sandbox_evidence_and_verification/` + `scripts/integration/os_sandbox_restriction_demo.sh` | `packages/skills-runtime-sdk-python/tests/test_os_sandbox_*.py` |
| CAP-SDK-006 Exec Sessions | `help/08-architecture-internals.cn.md`（runtime server） | `packages/skills-runtime-sdk-python/src/agent_sdk/runtime/server.py` | `examples/step_by_step/05_exec_sessions_across_processes/` | `packages/skills-runtime-sdk-python/tests/test_tools_exec_sessions_*` |
| CAP-SDK-007 Collab | `help/06-tools-and-safety.cn.md`（collab tools） | `packages/skills-runtime-sdk-python/src/agent_sdk/tools/collab.py` | `examples/step_by_step/06_collab_across_processes/` | `packages/skills-runtime-sdk-python/tests/test_tools_collab_*` |
| CAP-SDK-008 Skills | `help/05-skills-guide.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/skills/*` | `examples/skills/01_skills_preflight_and_scan/` | `packages/skills-runtime-sdk-python/tests/test_skills_*.py` |
| CAP-SDK-009 Sources | `help/05-skills-guide.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/skills/sources/*` |（集成脚本）`scripts/integration/skills_sources_docker_no_down.sh` | `packages/skills-runtime-sdk-python/tests/test_skills_sources_*.py` |
| CAP-SDK-010 State/WAL | `help/08-architecture-internals.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/state/*` | `examples/state/01_wal_replay_and_fork/` | `packages/skills-runtime-sdk-python/tests/test_agent_resume_replay.py` |
| CAP-SDK-012 Studio | `help/07-studio-guide.md` | `packages/skills-runtime-studio-mvp/backend/src/studio_api/app.py` | `help/examples/studio-api.http` | `packages/skills-runtime-studio-mvp/**/tests/*` |
| CAP-SDK-014 Plan + Input | `help/04-cli-reference.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/tools/plan_and_input.py` | `examples/step_by_step/08_plan_and_user_input/` | `packages/skills-runtime-sdk-python/tests/test_tools_update_plan.py` + `packages/skills-runtime-sdk-python/tests/test_tools_request_user_input.py` |
| CAP-SDK-015 Web/Image | `help/06-tools-and-safety.cn.md` | `packages/skills-runtime-sdk-python/src/agent_sdk/tools/web.py` | `examples/tools/02_web_search_disabled_and_fake_provider/` + `examples/workflows/19_view_image_offline/` | `packages/skills-runtime-sdk-python/tests/test_tools_web_search.py` + `packages/skills-runtime-sdk-python/tests/test_examples_smoke.py -k workflows_19` |

---

## 例：如何证明“完成了而且完整”

对某个 CAP-*，至少给出：
1. 契约入口（help + code）
2. examples（可运行证明）
3. tests（离线回归）
4. evidence（命令 + 结果 + 关键决策；可写在 PR/issue 或内部系统）

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
  - 产物：workspace 下生成 `report.md`，包含各子 agent 的 `wal_locator`
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

新增（更偏“具体落地场景”，仍保持离线可回归）：
- `examples/workflows/16_rules_based_parser/`：
  - 规则驱动结构化解析：自然语言规则 → plan.json → 确定性执行 result.json
  - 产物：`plan.json` + `result.json` + `report.md`
- `examples/workflows/17_minimal_rag_stub/`：
  - 最小 RAG（离线 stub）：kb_search（关键词检索）→ retrieval.json → report.md
  - 产物：`retrieval.json` + `report.md`
- `examples/workflows/18_fastapi_sse_gateway_minimal/`：
  - FastAPI/SSE 网关最小骨架：create run → SSE stream → approvals decide → terminal event
  - 产物：`report.md` + wal_locator（默认 file WAL 下为 `.skills_runtime_sdk/runs/<run_id>/events.jsonl`）
- `examples/workflows/19_view_image_offline/`：
  - 离线 view_image：生成 PNG → view_image → image_meta.json/report.md
  - 产物：`generated.png` + `image_meta.json` + `report.md`
- `examples/workflows/20_policy_compliance_patch/`：
  - Policy 合规补丁：skill_ref_read 读取 references/policy.md → apply_patch 修复 target.md → artifacts 落盘
  - 产物：`target.md` + `patch.diff` + `result.md` + `report.md`
- `examples/workflows/21_data_import_validate_and_fix/`：
  - 数据导入校验与修复：read_file → file_write → shell_exec(QA) → report.md
  - 产物：`fixed.csv` + `validation_report.json` + `report.md`
- `examples/workflows/22_chatops_incident_triage/`：
  - ChatOps 排障：read_file(incident.log) → request_user_input 澄清 → update_plan 推进 → file_write(runbook/report)
  - 产物：`incident.log` + `runbook.md` + `report.md`
