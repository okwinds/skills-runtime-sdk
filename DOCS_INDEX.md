# skills-runtime-sdk — 文档索引（导航）

> 本索引用于快速定位仓库核心入口文档。
> 说明：为降低主索引噪音，task summaries 与 OpenSpec 条目已拆分到独立索引文件。

## 入口（必读）

- `README.cn.md`（建议优先读）与 `README.md`：项目简介、仓库范围（SDK vs Studio MVP）、快速开始、验证命令入口
- `AGENTS.md`：协作“宪法”（Spec First + TDD + Worklog Gate + 索引维护）
- `docs/policies/`：门禁政策集合（spec-first / tdd-gate / worklog-rules / dev-cycle / code-style）
- `docs/worklog.md`：工作记录（关键命令 + 结果摘要 + 决策；用于可追溯）
- `docs/worklog-archive/`：worklog 月度归档（`YYYY-MM.md`；阈值日前历史记录搬移）
- `docs/worklog-recent/`：近期大体积条目拆分（`YYYY-MM-DD.md`；主 worklog 保留指针）
- `docs/backlog.md`：未尽事宜唯一入口（future/todo 的集中清单）

## Specs（规格导航）

- `docs/specs/SPEC_REGISTRY.yaml`：Spec 注册表（查 canonical spec 入口与模块边界）
- `docs/specs/skills-runtime-sdk/README.md`：通用 Skills Runtime SDK 规格入口（build-ready specs）
- `docs/specs/releases/`：发布规格目录（版本升级、tag/version 护栏、release notes 草案）
- `docs/specs/releases/2026-03-01-v0.1.8-release.md`：`v0.1.8` 发布规格（版本一致性、关键回归与 release notes 草案）
- `docs/specs/2026-03-25-governance-runtime-hardening-phase1.md`：L3 源规格：补齐治理门禁最小闭环，并修复 runtime timeout / stale cleanup / workspace 边界 / tool 并发事件缓冲问题。
- `docs/specs/skills-runtime-sdk/docs/llm-token-usage-events-v1.md`：源规格（L3）：LLM token usage 运行时事件（`completed.usage` / `llm_usage` / observability 汇总）契约。
- `docs/specs/skills-runtime-studio-mvp/SPEC.md`：Studio MVP canonical spec（下游示例不定义框架契约）

## Help（接入与运维）

- `help/README.md`：Help 总导航（按学习路径组织）
- `help/examples/`：可复制运行的配置/代码/API 示例

## Examples（可运行示例）

- `examples/README.md`：面向人类读者的示例总览（离线回归标记 `EXAMPLE_OK:` 约定 + 入口导航）
- `examples/apps/README.md`：应用示例索引（每个 app 的 `EXAMPLE_OK:` 标记速查）
- `examples/studio/README.md`：Studio 相关示例索引（下游 MVP）

## Studio MVP

- `examples/studio/mvp/README.md`：Studio MVP 体验入口（启动/验证命令）
- `examples/studio/mvp/DOCS_INDEX.md`：Studio MVP 子工程文档索引（指向 repo canonical specs/help/worklog/tests 入口）

## Policies（门禁政策）

- `docs/policies/spec-first.md`：Spec-First Gate（G1）
- `docs/policies/spec-first-checklist.md`：Spec-First 配套检查清单（编码前/后逐条核对）
- `docs/policies/tdd-gate.md`：测试驱动交付（G2）
- `docs/policies/worklog-rules.md`：工作记录规则（G3）
- `docs/policies/dev-cycle.md`：开发循环路由（判级 → Spec → TDD → 实现 → 验证 → 沉淀）
- `docs/policies/governance-gate.md`：治理报告 Gate（G11）的执行口径与报告解释规则
- `docs/policies/code-style.md`：代码风格路由

## Templates（模板）

- `docs/templates/spec-templates/`：Spec 模板目录（功能/API/组件规格模板）
- `docs/templates/task-summary-template.md`：任务总结模板
- `WORKLOG_TEMPLATE.md`：Worklog 模板
- `TASK_SUMMARY_TEMPLATE.md`：任务总结模板（仓库根目录备用）

## Task Summaries（任务总结）

- `docs/task-summaries/INDEX.md`：task summaries 独立索引（承接原 `DOCS_INDEX.md` 中全部条目）
- `docs/task-summaries/2026-03-25-governance-runtime-hardening-phase1.md`：补齐治理报告 Gate 最小闭环，修复 runtime timeout / live-but-unresponsive fail-closed / workspace 边界 / 并发 tool 事件缓冲隔离问题。
- `docs/task-summaries/2026-03-25-repo-hard-issues-recheck.md`：对仓库做第二轮只读硬伤复核，确认旧问题修复状态，并补充 runtime 活性、collab 契约漂移与 tool 旁路事件 flush 缺口。
- `docs/task-summaries/2026-03-25-runtime-liveness-hardening-change-created.md`：创建 OpenSpec change `runtime-liveness-hardening`，确认其 `spec-driven` 流程状态并提取首个 proposal 模板。
- `docs/task-summaries/2026-03-25-runtime-liveness-hardening-proposal-created.md`：为 `runtime-liveness-hardening` 创建 proposal，收敛新 capability `runtime-rpc-liveness`，并将 `design/specs` 解锁为 ready。
- `docs/task-summaries/2026-03-25-runtime-liveness-hardening-apply-ready.md`：按 `openspec-ff-change` 创建 `design/specs/tasks`，将 `runtime-liveness-hardening` 推进到 apply-ready。
- `docs/task-summaries/2026-03-25-runtime-liveness-hardening-apply.md`：实现 `runtime-liveness-hardening`，修复 runtime server 半开连接与 `collab.wait` 活性问题，并补齐离线回归与 OpenSpec tasks。
- `docs/task-summaries/2026-03-24-repo-hard-issues-explore.md`：使用 `openspec-explore` 对仓库做只读硬伤排查，收束治理门禁断裂、runtime 协议缺口与 Studio 打包边界问题。
- `docs/task-summaries/2026-03-12-capability-runtime-reinstall-align-0.1.9.md`：重新 editable 安装 `capability-runtime`，刷新已安装元数据到 `skills-runtime-sdk==0.1.9` 并验证无破损依赖。
- `docs/task-summaries/2026-03-12-capability-runtime-status-check.md`：核验当前环境中 `capability-runtime` editable 安装元数据是否已与 `skills-runtime-sdk 0.1.9` 对齐。
- `docs/task-summaries/2026-03-12-release-blockers-apply.md`：修复发布前最小 blocker：Tier-0 docstring 失败、workflow_dispatch 版本护栏缺口、README 默认门禁入口漂移与 release spec 索引可发现性。
- `docs/task-summaries/2026-03-12-release-readiness-review.md`：评估当前仓库是否适合立即升级版本/发版，给出发布阻塞项、门禁结果与版本语义判断建议。
- `docs/task-summaries/2026-03-12-openai-stream-options-generic-4xx-fallback-fix.md`：修复 OpenAI-compatible streaming 在 auto-injected `stream_options` 遇到 generic `400/422 bad request` 时不会 fail-open 的兼容性回退缺口。
- `docs/task-summaries/2026-03-11-llm-token-usage-events-v1-apply.md`：实现 LLM token usage 事件链（`completed.usage` → `llm_usage` → WAL metrics 汇总），并补齐离线回归。

## OpenSpec（变更包）

- `openspec/INDEX.md`：OpenSpec 独立索引（承接原 `DOCS_INDEX.md` 中全部条目）

## Logs（运行与治理产物）

- `logs/governance/latest-report.md`：最新治理巡检报告（由 `scripts/governance/governance-check.sh --full --report` 生成，供 G11 开场检查）

## Worklog & Summaries
- `docs/task-summaries/`：任务结束总结目录（每次结项一份）。
