# docs_for_coding_agent（给“编码智能体 / AI Coding Agent”的教学文档包）

本目录的目标：让编码智能体在 **不读全仓库** 的情况下，也能快速掌握 Skills Runtime SDK 的：
- 能力边界（能做什么 / 不能做什么）
- 最短路径（怎么跑通 / 怎么扩展）
- 质量门禁（怎么写测试、怎么证明“完整完成”）

适用对象：
- Coding Agent（Codex CLI / Claude Code / 其它）：需要接手任务、改代码、补测试、写文档
- 平台研发：需要扩展 tools / skills / sandbox / approvals / state

你可以这样用（推荐）：
1. 先读 `docs_for_coding_agent/cheatsheet.zh-CN.md`（10 分钟建立“命令与入口”心智模型）
2. 再读 `docs_for_coding_agent/capability-inventory.md`（全能力点 CAP-* 清单）
3. 需要落地时，按 `docs_for_coding_agent/capability-coverage-map.md` 找到对应：
   - specs（契约）
   - examples（可运行）
   - tests（离线回归）
   - help（接入/运维手册）

“怎么用”的最短路径：
- `docs_for_coding_agent/00-quickstart-offline.md`
- `docs_for_coding_agent/01-recipes.md`
- `docs_for_coding_agent/02-ops-and-qa.md`

注意：
- 本仓库的协作规则以 `AGENTS.md` 为准（Spec First + TDD Gate + Worklog + Task Summary + DOCS_INDEX）。
- 示例库在 `examples/`（本目录只讲“怎么用/怎么交付”，不放大段代码）。
