<div align="center">

[中文](00-overview.cn.md) | [English](00-overview.md) | [Help](README.cn.md)

</div>

# 00. 框架总览：你正在用什么系统

## 0.1 一句话定义

`skills-runtime-sdk` 是一个 **Skills-first 的 Skills Runtime 框架**：
- 用统一配置定义模型、工具、安全策略；
- 用标准事件流（WAL）记录运行过程；
- 用 Skills 机制把可复用能力注入到任务执行中；
- 用 Studio MVP 提供可视化会话与运行入口。

命名说明：
- 你依然通过 `Agent` API（`Agent.run()` / `Agent.run_stream()`）来运行任务，但从概念上 **Skills 才是一等扩展面**。
- `Agent` 是运行引擎：负责执行一次 run（Prompt 编译 → Skills 注入 → LLM → tool 编排 → WAL 事件落盘）。

## 0.2 仓库结构（只看关键）

```text
<repo_root>/
├── packages/
│   ├── skills-runtime-sdk-python/        # SDK Python 参考实现
│   └── skills-runtime-studio-mvp/        # Studio MVP（FastAPI + React）
├── help/                                 # 本手册（中英文）
├── examples/                             # 可直接复用的示例资源（skills 等）
└── scripts/                              # 回归与集成演示脚本
```

## 0.3 四层模型（强烈建议记住）

1. **Config Layer（配置层）**
   - 来源：内置默认配置（安装包路径：`skills_runtime/assets/default.yaml`）+ overlay YAML + env + session settings
   - 结果：有效运行配置（模型、超时、safety、sandbox、skills）

2. **Runtime Layer（运行层）**
   - 入口：`Agent.run()` / `Agent.run_stream()`
   - 核心：Prompt 编译、Skills 注入、LLM 请求、tool orchestration、事件落盘

3. **Safety Layer（安全层）**
   - 门禁：denylist / allowlist / approvals
   - 隔离：OS sandbox（seatbelt / bubblewrap）

4. **Product Layer（产品层）**
   - 例如 Studio MVP：会话管理、`filesystem_sources`、run、SSE、审批接口

## 0.4 关键术语速查

- **Skill mention**：合法格式为 `$[namespace].skill_name`（见 `help/13-namespace-mentions.cn.md`）
- **自由文本提取**：只提取合法 mention；不合法片段按普通文本处理
- **Tool 参数严格校验**：当参数要求 `skill_mention` 时仍必须是完整 token
- **Agent**：运行引擎实例（执行一次 run 并产出 WAL 事件）
- **Approval（门卫）**：决定是否允许动作执行
- **Sandbox（围栏）**：允许执行后仍限制执行边界
- **WAL（events.jsonl）**：运行事件审计日志，排障第一现场

## 0.5 SDK 与 Studio 的关系

- SDK 是 runtime 内核，可直接被 Python 项目调用；
- Studio MVP 是 SDK 的“最小可运行产品壳”，用于快速验证与演示：
  - 后端：复用 SDK + 暴露 REST/SSE
  - 前端：会话、skills、运行、审批交互

## 0.6 这套框架适合什么场景

适合：
- 你需要把“可复用技能 + 工具调用 + 安全门禁 + 事件审计”打包成运行时
- 你需要既有 CLI/脚本化入口，又有 Web 体验入口

不适合：
- 只想写单次 prompt、无运行时治理需求
- 只做前端页面，不关心 agent runtime

## 0.7 下一步

继续看 `help/01-quickstart.cn.md`，先跑通最小链路。

---

上一章：[Help 总览](README.cn.md) · 下一章：[01. 快速开始](01-quickstart.cn.md)
