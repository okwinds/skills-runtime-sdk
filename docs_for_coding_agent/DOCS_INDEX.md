# Docs Index（docs_for_coding_agent/）

本文件是 `docs_for_coding_agent/` 的文档索引（目录级“目录”），用于快速定位关键材料。

约定：
- 每条记录必须包含：文件路径/文件名 + 一句话说明
- 新增/删除/重命名文档时必须同步更新本索引

---

## Core

- `docs_for_coding_agent/README.md`：本目录用途与推荐阅读路径。
- `docs_for_coding_agent/00-quickstart-offline.md`：离线最短跑通（以 examples 为准）。
- `docs_for_coding_agent/01-recipes.md`：场景化配方（怎么用到具体场景里）。
- `docs_for_coding_agent/02-ops-and-qa.md`：验收/回归/排障（证据字段与分层门禁）。
- `docs_for_coding_agent/03-workflows-guide.md`：Workflows 指南（Skills-First：用 skills 组装单/多 agent 项目级示范）。
- `docs_for_coding_agent/workflows-applied-scenarios-18-20-22.md`：Applied Scenarios workflows（18/20/22）的目标/约束/验收与离线回归口径。
- `docs_for_coding_agent/cheatsheet.zh-CN.md`：中文速查表（最常用命令、入口与排障）。
- `docs_for_coding_agent/cheatsheet.en.md`：英文速查表（便于英文指令场景快速定位）。

---

## Capability

- `docs_for_coding_agent/capability-inventory.md`：能力清单（CAP-*，以“不遗漏”为准绳）。
- `docs_for_coding_agent/capability-coverage-map.md`：CAP-* → specs/help/examples/tests 的覆盖映射（交付与审计入口）。

---

## Delivery Contract

- `docs_for_coding_agent/task-contract.md`：任务契约（交付口径：Spec/TDD/Worklog/Task Summary/Index）。
- `docs_for_coding_agent/testing-strategy.md`：测试策略（离线门禁 vs 可选集成；fake backend 的使用方式）。
- `docs_for_coding_agent/common-pitfalls.md`：常见坑与排障（含 Docker/沙箱可用性、忽略规则等）。

---

## Examples（Coding Agent）

> 本节仅索引“面向编码智能体”的能力覆盖示例代码（默认离线可回归）。

- `docs_for_coding_agent/examples/step_by_step/`：按学习路径组织的最小闭环示例（tool_calls/approvals/sandbox/WAL…）。
- `docs_for_coding_agent/examples/tools/`：ToolRegistry 与工具协议示例（尽量不依赖 LLM）。
- `docs_for_coding_agent/examples/skills/`：skills preflight/scan/mentions 示例。
- `docs_for_coding_agent/examples/state/`：WAL replay/fork 示例。
