# workflows（项目级示范：Skills-First 组合编排）

本目录提供“项目级（project-level）”示范：强调 **以 Skills（`SKILL.md`）为最小单元** 构建 agent 能力，再用少量编排代码把多个 skills 组合成可落地的复杂场景。

与其它示例层的关系：
- `examples/step_by_step/`：按学习路径理解 SDK 原语（tool_calls、approvals、sandbox、WAL…）
- `examples/tools/` / `examples/skills/` / `examples/state/`：按主题拆分的能力点示例
- `examples/workflows/`：把多个能力点组合成“能做项目”的流水线形态（推荐在看完 step_by_step 后阅读）

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
