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
   - 契约入口（以 `help/` 为主，必要时指向源码入口）
   - examples（可运行）
   - tests（离线回归）
   - help（接入/运维手册）

“怎么用”的最短路径：
- `docs_for_coding_agent/00-quickstart-offline.md`
- `docs_for_coding_agent/01-recipes.md`
- `docs_for_coding_agent/02-ops-and-qa.md`

注意：
- 本目录是 **对外教学材料**：不依赖也不引用任何“本地协作文档”（例如内部 worklog/backlog/协作宪法文件）。
- 若你在内部生产环境有额外门禁要求（例如强制要求某些本地协作文件存在），建议用“私有注入/挂载”实现，并通过 `REQUIRE_LOCAL_DOCS=1` 显式开启校验（入口见 `help/12-validation-suites.cn.md`）。
- 编码智能体示例库在 `docs_for_coding_agent/examples/`（能力覆盖与教学导向；默认离线可回归）。
- 面向人类的应用示例在 `examples/`（更强调“跑起来像小应用”的体验；提供真模型运行方式）。
