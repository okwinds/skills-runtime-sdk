# workflows（项目级示范：Skills-First 组合编排）

本目录提供“项目级（project-level）”示范：强调 **以 Skills（`SKILL.md`）为最小单元** 构建 agent 能力，再用少量编排代码把多个 skills 组合成可落地的复杂场景。

与其它示例层的关系：
- `docs_for_coding_agent/examples/step_by_step/`：按学习路径理解 SDK 原语（tool_calls、approvals、sandbox、WAL…）
- `docs_for_coding_agent/examples/tools/` / `docs_for_coding_agent/examples/skills/` / `docs_for_coding_agent/examples/state/`：按主题拆分的能力点示例（更偏教学/覆盖）
- `docs_for_coding_agent/examples/workflows/`：把多个能力点组合成“项目级组合示范”的工作流形态（推荐在理解原语后阅读）

补充：
- 面向人类的“应用示例”（更强调交互与体验）统一放在 `examples/`。

约束（门禁友好）：
- 默认离线可运行（Fake backend + scripted approvals）
- 每个角色能力必须有对应 skill，并通过 mention 注入触发 `skill_injected` 事件
- 示例应产出可检查的 workspace 产物（例如 `report.md`）与 WAL（`events.jsonl`）

离线 smoke tests（门禁）：
- `pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py`

## 示例列表

1. `01_multi_agent_repo_change_pipeline/`
   - 多 agent 协作流水线：分析 → 打补丁 → 运行校验 → 生成报告
   - 强调 skills-first：每个角色能力都来自 `skills/*/SKILL.md`

2. `02_single_agent_form_interview/`
   - 单 agent 多轮表单访谈：request_user_input → update_plan → file_write → shell_exec
   - 强调 skills-first：访谈/校验/落盘能力都来自 skills

3. `03_multi_agent_reference_driven_pipeline/`
   - 多 agent references 驱动：skill_ref_read 读取 policy → patch/qa/report
   - 强调 skill bundle references 的可复用“规则/政策”形态

4. `04_map_reduce_parallel_subagents/`
   - 总分总结构：Planner 拆解 subtasks.json → 子 agent 并行执行 → Aggregator 汇总 report.md
   - 强调并行与独立产物：每个子任务互不影响

5. `05_multi_agent_code_review_fix_qa_report/`
   - 多 agent：review（只读）→ fix（apply_patch）→ qa（shell_exec）→ report（file_write）
   - 强调 reviewer 的“只读边界”与后续修复/验证/汇总的证据链

6. `06_wal_fork_and_resume_pipeline/`
   - 断点续做：第一次 run 中断 → fork_run 生成新 run → replay resume 继续完成
   - 强调 WAL fork 与 run_started.resume 证据

7. `07_skill_exec_actions_module/`
   - actions：在 skill bundle 内声明 frontmatter `actions`，运行期用 skill_exec 执行
   - 强调 actions 默认禁用、路径逃逸防护与 approvals/sandbox 证据链

8. `08_studio_sse_integration/`
   - Studio 集成：create session/run → SSE stream → 自动 approvals → 等待 terminal event
   - 默认不进入离线门禁（需要显式 opt-in）

9. `09_branching_router_workflow/`
   - 路由分支：router 生成 route.json → worker 执行分支 → reporter 汇总 report.md
   - 强调把“分支决策”落为可审计产物

10. `10_retry_degrade_workflow/`
   - 重试→降级：attempt 失败达到预算后生成 fallback，并汇总 exit_code/证据指针
   - 强调“失败也必须可审计”

11. `11_collab_parallel_subagents_workflow/`
   - collab 原语：master 在 agent loop 内调用 spawn_agent/send_input/wait 管理子 agent
   - 强调“子 agent 生命周期工具链”与 Skills-First 的结合

12. `12_exec_sessions_engineering_workflow/`
   - exec sessions：在 agent loop 内调用 exec_command/write_stdin 完成交互式工程流
   - 强调“交互式会话也要有 WAL/approvals 证据链”

15. `15_workflow_eval_harness/`
   - eval harness：同一 workflow 跑多次，对比 artifacts，一致性评分并输出 diff 摘要
   - 强调把 workflow 当作“可评测对象”，利于 CI/回归护栏

16. `16_rules_based_parser/`
   - 规则驱动的结构化解析：自然语言规则 → 可执行 plan.json → 确定性执行 result.json
   - 强调 skills-first + `file_write` 产物落盘 + WAL 审计证据链

17. `17_minimal_rag_stub/`
   - 最小 RAG（离线 stub）：自定义 `kb_search`（关键词检索）→ retrieval.json → report.md
   - 强调离线可回归（不依赖向量库/外网）+ `skill_injected` 证据

18. `18_fastapi_sse_gateway_minimal/`
   - FastAPI/SSE 网关最小骨架：create run → SSE stream → 自动 approvals decide → terminal event
   - 若缺少 fastapi/uvicorn：明确 SKIP 原因但仍输出 EXAMPLE_OK（避免门禁不稳定）

19. `19_view_image_offline/`
   - 离线 view_image：生成小 PNG → `view_image` 读取 → image_meta.json/report.md
   - 强调 image tool 的可审计证据链（WAL：tool_call_finished(view_image)）

20. `20_policy_compliance_patch/`
   - Policy 合规补丁：`skill_ref_read` 读取 references/policy.md → `apply_patch` 修复 target.md → 产物落盘
   - 强调 references 的“可分发政策”形态 + approvals/WAL 证据链

21. `21_data_import_validate_and_fix/`
   - 数据导入校验与修复：read_file(input.csv) → 写 fixed.csv/报告 → shell_exec(QA) → report
   - 强调“确定性修复规则 + QA 护栏 + WAL 证据指针”

22. `22_chatops_incident_triage/`
   - ChatOps 排障：read_file(incident.log) → request_user_input 澄清 → update_plan 推进 → file_write(runbook/report)
   - 强调 human_request/human_response + plan_updated + approvals/WAL 证据链
