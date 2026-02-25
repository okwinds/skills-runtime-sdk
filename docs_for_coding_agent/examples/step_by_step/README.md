# step_by_step（按学习路径组织）

目的：让读者（尤其是编码智能体）用最短路径理解 SDK 的核心闭环：

1. `01_offline_minimal_run/`：离线最小 run（FakeChatBackend）
2. `02_offline_tool_call_read_file/`：离线 tool_calls（read_file）→ 工具执行 → 回注 → 完成
3. `03_approvals_and_safety/`：审批 ask/deny + approved_for_session 缓存语义
4. `04_sandbox_evidence_and_verification/`：`data.sandbox` 证据字段（离线 meta + 真实验证入口）
5. `05_exec_sessions_across_processes/`：exec sessions 跨进程复用（tools CLI）
6. `06_collab_across_processes/`：collab primitives 跨进程复用（tools CLI）
7. `07_skills_references_and_actions/`：skills references + actions（skill_ref_read + skill_exec + 审批）
8. `08_plan_and_user_input/`：计划与结构化人机输入（update_plan + request_user_input）
