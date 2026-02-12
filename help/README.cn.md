<div align="center">

[中文](README.cn.md) | [English](README.md)

</div>

# Skills Runtime SDK / Studio Help 文档总览

> 这是一套“可以直接拿来用”的框架手册。目标不是解释概念，而是让你**照着做就能跑通、出错能定位、上线可审计**。

## 适用对象

- 业务研发：需要把 SDK 接进现有产品
- 平台研发：需要扩展 tools / skills / approvals / sandbox
- 测试与运维：需要确定回归口径、排障流程、上线检查项

## 阅读顺序（强烈建议）

1. `help/00-overview.cn.md`：先建立整体认知（模块、边界、术语）
2. `help/01-quickstart.cn.md`：10~20 分钟跑通最小链路
3. `help/02-config-reference.cn.md`：掌握配置优先级与安全默认值
4. `help/04-cli-reference.cn.md`：命令行能力与返回码
5. `help/07-studio-guide.cn.md`：Studio MVP 后端/前端/接口全流程
6. `help/09-troubleshooting.cn.md`：常见故障快速定位与修复

英文版对应入口：
- `help/00-overview.md`
- `help/01-quickstart.md`
- `help/02-config-reference.md`
- `help/04-cli-reference.md`
- `help/07-studio-guide.md`
- `help/09-troubleshooting.md`

## 文档地图

- 00 总览：`help/00-overview.cn.md` ｜ English：`help/00-overview.md`
- 01 快速开始：`help/01-quickstart.cn.md` ｜ English：`help/01-quickstart.md`
- 02 配置参考：`help/02-config-reference.cn.md` ｜ English：`help/02-config-reference.md`
- 03 Python API：`help/03-sdk-python-api.cn.md` ｜ English：`help/03-sdk-python-api.md`
- 04 CLI：`help/04-cli-reference.cn.md` ｜ English：`help/04-cli-reference.md`
- 05 Skills：`help/05-skills-guide.cn.md` ｜ English：`help/05-skills-guide.md`
- 06 Tools + Safety：`help/06-tools-and-safety.cn.md` ｜ English：`help/06-tools-and-safety.md`
- 07 Studio：`help/07-studio-guide.cn.md` ｜ English：`help/07-studio-guide.md`
- 08 内部机制：`help/08-architecture-internals.cn.md` ｜ English：`help/08-architecture-internals.md`
- 09 排障：`help/09-troubleshooting.cn.md` ｜ English：`help/09-troubleshooting.md`
- 10 Cookbook：`help/10-cookbook.cn.md` ｜ English：`help/10-cookbook.md`
- 11 FAQ：`help/11-faq.cn.md` ｜ English：`help/11-faq.md`

补充专题（不按编号顺序阅读）：
- Sandbox 最佳实践：`help/sandbox-best-practices.cn.md` ｜ English：`help/sandbox-best-practices.md`

## 示例资源

- `help/examples/sdk.overlay.yaml`：SDK 通用 overlay 示例
- `help/examples/skills.cli.overlay.yaml`：Skills CLI 最小扫描配置
- `help/examples/studio.runtime.overlay.yaml`：Studio MVP 平衡模式配置示例
- `help/examples/run_agent_minimal.py`：Python 最小运行示例
- `help/examples/run_agent_with_custom_tool.py`：自定义 tool 示例
- `help/examples/studio-api.http`：Studio API 调用样例

## 重要约束

- 本框架默认要求 Python `>=3.10`（`packages/skills-runtime-sdk-python/pyproject.toml`）
- 文档示例默认使用 `<repo_root>` 占位，不写机器绝对路径
- secrets 不入库：只使用 `.env.example` + 本地 `.env`
- 建议把 Help 当作“可直接使用的操作手册”；如需更深的实现细节，再看对应源码（`packages/skills-runtime-sdk-python/src/agent_sdk/*`）

## 一条建议

如果你是第一次接入：
- 不要一上来就改框架代码；
- 先跑 `quickstart` 和 `CLI preflight/scan`，把环境、配置、skills 目录打通；
- 再按 `cookbook` 选择“低打扰”或“高安全”策略。
