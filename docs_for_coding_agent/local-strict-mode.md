# Local / Strict Mode 桥接说明

本文件用于桥接两类常见的交付语境：

1) **Public teaching pack（对外教学包）**：`docs_for_coding_agent/`  
   目标是帮助编码智能体快速上手，不强制依赖你们团队的本地协作文档体系。

2) **Local / Strict Mode（本地门禁模式）**：当仓库存在本地门禁（例如 `AGENTS.md`、`docs/policies/*`），或显式启用校验开关（例如 `REQUIRE_LOCAL_DOCS=1`）时，交付必须遵循本地门禁定义的 DoD。

---

## 何时你必须按“本地门禁”交付？

满足任一条件，即视为进入 Local / Strict Mode：

- 仓库根目录存在 `AGENTS.md`（或等价的协作宪法/门禁文件）
- CI / 脚本启用了 `REQUIRE_LOCAL_DOCS=1`
- 团队明确要求：Spec-First + TDD + Worklog + Task Summary + Index

---

## 在本仓库（含门禁文件）你应遵循哪些权威入口？

最小必读（建议顺序）：

- `docs/policies/dev-cycle.md`：开发循环（判级 → Spec → RED → GREEN → VERIFY → DISTILL）
- `docs/policies/spec-first.md`：写代码前必须先有 `docs/specs/**` 源规格
- `docs/policies/tdd-gate.md`：没有通过测试不算完成（命令 + 完整结果要记录）
- `docs/policies/worklog-rules.md`：工作记录格式与“每个动作都要记”的硬要求

交付沉淀（DoD 相关）：

- `docs/worklog.md`：本仓库工作记录（命令/结果/决策证据链）
- `docs/task-summaries/`：每次任务结束必须产出一份任务总结（并登记索引）
- `DOCS_INDEX.md`：新增/删除/重命名文档必须同步登记（路径 + 一句话说明）

---

## Public vs Strict 的边界（避免误导）

- `docs_for_coding_agent/` 仍然是对外教学入口：内容尽量保持可迁移、可复用，不把流程写死为某个团队工具链。
- 但当你处在“有本地门禁”的仓库里，**本地门禁优先**：即使教学包没有写到某个要求，你也必须遵循仓库内的门禁文件。

