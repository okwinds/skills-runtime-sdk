# Workflows Guide（Skills-First 项目级示范如何复刻/扩展）

本页目标：像 Agently 的 `ai_coding_guide_with_agently.md` 一样，提供一套“可直接照着做”的 workflow 落地方式，但遵循本仓库的核心原则：

> **所有 agent 能力都必须基于 Skills（`SKILL.md`）最小单元构建。**

你可以把它理解为：先定义 Skills（角色能力），再写编排层（把多个 skills 组合成流水线）。

---

## 1) 快速开始：直接跑现成 workflows

建议先跑离线门禁（包含 workflows）：

```bash
bash scripts/pytest.sh
```

然后单独运行任一 workflow 示例（示例）：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/01_multi_agent_repo_change_pipeline/run.py --workspace-root /tmp/srsdk-demo
```

当前 workflows：
- `examples/workflows/01_multi_agent_repo_change_pipeline/`：多 agent 代码修复流水线（Analyze→Patch→QA→Report）
- `examples/workflows/02_single_agent_form_interview/`：单 agent 多轮表单访谈（Human I/O + Plan + 产物落盘）
- `examples/workflows/03_multi_agent_reference_driven_pipeline/`：references 驱动（skill_ref_read 读取 policy）
- `examples/workflows/04_map_reduce_parallel_subagents/`：总分总 + 并行子任务（map-reduce 形态）
- `examples/workflows/05_multi_agent_code_review_fix_qa_report/`：Review→Fix→QA→Report（带 code review 的流水线）
- `examples/workflows/06_wal_fork_and_resume_pipeline/`：断点续做（WAL fork + replay resume）
- `examples/workflows/07_skill_exec_actions_module/`：Skill Actions（skill_exec 执行动作脚本）
- `examples/workflows/08_studio_sse_integration/`：Studio API + SSE 端到端（集成示例，需显式 opt-in）
- `examples/workflows/09_branching_router_workflow/`：路由分支（router → worker → report）
- `examples/workflows/10_retry_degrade_workflow/`：重试→降级→报告（失败也可审计）
- `examples/workflows/11_collab_parallel_subagents_workflow/`：collab 原语并行子 agent（spawn/wait/send_input）
- `examples/workflows/12_exec_sessions_engineering_workflow/`：exec sessions 工程式交互（exec_command/write_stdin）
- `examples/workflows/15_workflow_eval_harness/`：workflow eval harness（多次运行对比 artifacts + score）
- `examples/workflows/16_rules_based_parser/`：规则驱动结构化解析（规则→plan.json→确定性执行→result.json）
- `examples/workflows/17_minimal_rag_stub/`：最小 RAG（离线 stub：kb_search → retrieval.json → report.md）
- `examples/workflows/18_fastapi_sse_gateway_minimal/`：FastAPI/SSE 网关最小骨架（离线）
- `examples/workflows/19_view_image_offline/`：离线 view_image（生成 PNG → view_image → 产物落盘）
- `examples/workflows/20_policy_compliance_patch/`：policy 合规补丁（references/policy.md → apply_patch → artifacts）
- `examples/workflows/21_data_import_validate_and_fix/`：数据导入校验与修复（确定性规则 + QA 护栏）
- `examples/workflows/22_chatops_incident_triage/`：ChatOps 排障（澄清→计划→runbook/report）

---

## 2) Workflows 的最小“骨架形态”

一个可回归、可审计的 workflow，建议满足：

1. **技能包（skills/）**：每个角色一个 `SKILL.md`
2. **编排脚本（run.py）**：只负责组合 skills + 驱动 agent(s) + 汇总结果
3. **离线确定性**：
   - LLM：Fake backend（`FakeChatBackend`）
   - approvals：scripted `ApprovalProvider` 自动批准（避免示例阻塞）
   - human I/O（如需要）：scripted `HumanIOProvider`
4. **证据链**：
   - WAL：`events.jsonl`（至少出现 `skill_injected`）
   - 产物：workspace 下写一个可检查文件（例如 `report.md` / `submission.json`）

推荐目录结构：

```
examples/workflows/<nn_name>/
  README.md
  run.py
  skills/
    <skill_name>/
      SKILL.md
      references/   # 可选：policy/规范/例子
      actions/      # 可选：skill_exec 的脚本（如启用）
```

---

## 3) Skills-First 的关键落地点（别只停在口号）

### 3.1 每个角色必须有 Skill

例如（多 agent 流水线）：
- Analyze：`repo_analyzer`
- Patch：`repo_patcher`
- QA：`repo_qa`
- Report：`repo_reporter`

这些角色能力必须体现在：
- `skills/<skill>/SKILL.md`（可复用说明）
- 任务文本中显式包含对应 mention（触发注入 + 证据事件）

### 3.2 任务文本必须带 mention（触发注入与审计证据）

示例：

```text
$[examples:workflow].repo_patcher
请修复 app.py 的 bug，并给出最小补丁。
```

运行后你应该在 WAL 中看到：
- `type=skill_injected`
- `payload.mention_text="$[examples:workflow].repo_patcher"`

---

## 4) 什么时候用 references（skill_ref_read）

当你希望“规则/政策/标准/说明材料”可复用、可随 skill 一起分发时，把它们放入：
- `skills/<skill>/references/*`

运行期用 `skill_ref_read` 读取（示例见 `examples/workflows/03_multi_agent_reference_driven_pipeline/`）。

注意：
- `skill_ref_read` 默认 fail-closed，需要在 overlay 显式开启 `skills.references.enabled=true`。
- 引用路径必须在 `references/` 下（默认不允许 `assets/`）。

---

## 5) 什么时候用 human I/O（request_user_input）

当 workflow 需要明确的“人类决策点”或“结构化数据输入”，优先使用：
- `request_user_input`（结构化题目 + 选项）
- `update_plan`（把关键推进过程结构化可见）

示例见 `examples/workflows/02_single_agent_form_interview/`。

---

## 6) 总分总 + 并行子任务（Map-Reduce 编排）

当你需要“总任务 → 拆分多个互不依赖子任务 → 并行执行 → 汇总报告”，推荐使用如下骨架：

1. Planner：拆解出 `subtasks.json`（含每个子任务的产物路径）
2. Subagents：并行执行，每个子任务只写自己的产物（互不影响）
3. Aggregator：汇总产物 + wal_locator 指针，生成 `report.md`

对应示例：
- `examples/workflows/04_map_reduce_parallel_subagents/`

验收观察点：
- `subtasks.json` 存在且结构稳定（可迁移到真实项目）
- 子任务产物互不覆盖（例如 `outputs/*.md`）
- `report.md` 包含每个子任务的 `wal_locator`

---

## 7) Review→Fix→QA→Report（把 code review 变成流水线）

当你希望把“review 的只读边界”与“修复/验证/汇总的副作用”拆开，推荐：
- Reviewer：只读（read_file/grep_files）
- Fixer：只写（apply_patch）
- QA：只执行（shell_exec）
- Reporter：只写（file_write）

对应示例：
- `examples/workflows/05_multi_agent_code_review_fix_qa_report/`

---

## 8) 断点续做（WAL fork + replay resume）

当长任务中途失败/进程崩溃，你希望从“已完成的断点”继续跑：

- 选择 fork 点（通常是“最后一次成功关键工具调用”）
- `fork_run(...)` 生成新 run_id 的 WAL 前缀
- 用 `run.resume_strategy=replay` 运行新 run，尽量恢复 tool outputs 与 approvals cache

对应示例：
- `examples/workflows/06_wal_fork_and_resume_pipeline/`

验收观察点：
- `run_started.payload.resume.enabled == true` 且 `strategy == replay`
- 新 run 继续写入新 WAL，并产出后续产物（例如 `final.txt`）

---

## 9) Skill Actions（skill_exec）

当你希望把“可执行动作”随 Skill 一起打包（而不是让 LLM 临时拼命令），推荐：
- 脚本放 `actions/`
- 在 `SKILL.md` frontmatter 的 `actions` 声明 action（argv/timeout/env）
- 运行期显式开启 `skills.actions.enabled=true`（默认禁用）
- 通过 builtin tool `skill_exec` 执行动作（仍走 approvals/sandbox/WAL 证据链）

对应示例：
- `examples/workflows/07_skill_exec_actions_module/`

---

## 10) Studio 集成（API + SSE）

当你需要把“编排能力”做成服务/产品形态（UI/后端）并提供 SSE 事件流：

- create session（设置 skills roots）
- create run（message 含 skill mention）
- subscribe SSE：`/api/v1/runs/<run_id>/events/stream`
- approvals：监听 `approval_requested` 并调用 decide API

对应示例（集成，需显式 opt-in）：
- `examples/workflows/08_studio_sse_integration/`

离线最小骨架（不依赖 Studio）：
- `examples/workflows/18_fastapi_sse_gateway_minimal/`

---

## 11) 路由分支（Router pattern）

当你需要“同一入口任务 → 按条件走不同分支 → 最后汇总成同一份报告”，推荐把“路由决策”落为可审计产物：

- Router：读取输入（例如 `task_input.json`）→ 写 `route.json`
- Worker(A/B/...)：各自只写自己的产物（例如 `outputs/path_a.md`）
- Reporter：汇总写 `report.md`（包含 `wal_locator` 指针）

对应示例：
- `examples/workflows/09_branching_router_workflow/`

验收观察点：
- `route.json` 存在且结构稳定（可迁移到真实项目）
- `report.md` 能指向路由与分支产物及证据路径

---

## 12) 重试→降级→报告（Retry + Degrade + Report）

当你面对“外部依赖不稳定/工具可能失败”的真实项目，推荐用“可审计的失败 + 有预算的重试 + 最终降级”的骨架：

1. Controller：写重试预算（例如 2 次）与降级策略（`retry_plan.json`），并用 `update_plan` 同步进度
2. Attempt：用 `shell_exec` 做尝试（允许失败，但必须留证据：exit_code/ok）
3. Degrade：预算耗尽后，生成 fallback 产物（例如 `outputs/fallback.md`）
4. Reporter：写 `report.md`（汇总每次 attempt 的 exit_code 与 `wal_locator`）

对应示例：
- `examples/workflows/10_retry_degrade_workflow/`

验收观察点：
- attempt 的 `tool_call_finished.ok == false` 也必须存在（失败仍可审计）
- `report.md` 中必须体现 exit_code 与降级结论

---

## 13) Collab 原语并行子 agent（spawn/wait/send_input）

当你希望主 agent 用“工具原语”管理子 agent 生命周期（而不是编排层自己开线程池），使用：
- `spawn_agent` / `wait` / `send_input` / `close_agent`

这类模式适合：
- 子任务数量不固定（动态扩容/缩容）
- 需要在执行中途给子 agent 追加输入（例如补充约束/数据）

对应示例：
- `examples/workflows/11_collab_parallel_subagents_workflow/`

验收观察点：
- master 的 WAL 中 `spawn_agent/send_input/wait` 都应有 `tool_call_finished.ok == true`
- 子 agent 仍然必须 Skills-First（各自有 `skill_injected` + 独立 WAL + 独立产物）

---

## 14) exec sessions 工程式交互（exec_command/write_stdin）

当你需要实现“工程师常见的交互式 CLI 流程”（例如 REPL/生成器/交互脚本），使用：
- `exec_command` 启动 PTY 会话
- `write_stdin` 写入输入并轮询输出

对应示例：
- `examples/workflows/12_exec_sessions_engineering_workflow/`

验收观察点：
- `exec_command/write_stdin` 都应留下 approvals/WAL 证据链
- 报告建议只记录“关键标记是否出现”，避免 PTY 输出细节导致回归不稳定

---

## 15) workflow eval harness（多次运行对比 artifacts）

当你希望把 workflow 当作“可评测对象”（而不是一次性脚本），建立回归护栏时，推荐提供 eval harness：
- 同一 workflow 运行 N 次
- 收集关键 artifacts（`report.md`、`outputs/*` 等）
- normalize（去除 workspace 绝对路径、WAL run_id 等噪音）
- 输出 score + diff 摘要（Markdown + JSON）

对应示例：
- `examples/workflows/15_workflow_eval_harness/`

验收观察点：
- `eval_score.json` 可被 CI 读取
- diff 摘要能快速定位“哪一个 artifact 在哪一次 run 出现不一致”
