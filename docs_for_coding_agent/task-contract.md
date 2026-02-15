# Task Contract（任务契约：如何在本仓库“完整交付”）

本契约用于约束编码智能体与人类协作的交付形态，目标是：
- 交付可复现（别人能跑）
- 交付可回归（有离线门禁）
- 交付可审计（worklog + task summary + 索引可定位）

本仓库的更高优先级规则见：`AGENTS.md`。

---

## 1) 变更分级（必须先判断）

- L0：纯文档更新（更新索引；通常无需新增测试）
- L1：小改动/小 bugfix（写简短 spec；补单测）
- L2：功能交付（写完整 spec；单测 + 至少 1 个集成/场景回归）
- L3：高风险改造（完整 spec + 风险/回滚；单测 + 集成 + 护栏）

## 2) 必交付物（除非明确说明不需要）

1. Spec（先于代码）：
   - `Goal / Constraints / Contract / Acceptance Criteria / Test Plan`
2. Tests（离线回归门禁）：
   - 新增/修复必须有新增/更新测试
3. Docs index：
   - `DOCS_INDEX.md`（新增文档/示例必须可定位）
4. Worklog（可追溯）：
   - `docs/worklog.md` 记录关键命令 + 结果 + 决策
5. Task Summary（结项复盘）：
   - `docs/task-summaries/<YYYY-MM-DD-...>.md`

## 3) Done 的定义（不要“最小化完成”）

从 backlog（BL-*）移出（或进入 done memo）的前提：
- 能力不是“能跑一次”，而是：
  - contract 明确
  - 离线回归覆盖（happy path + 关键错误路径）
  - 可观测字段齐全
  - 文档与示例可复用

