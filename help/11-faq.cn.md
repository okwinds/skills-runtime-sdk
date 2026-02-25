<div align="center">

[中文](11-faq.cn.md) | [English](11-faq.md) | [Help](README.cn.md)

</div>

# 11. FAQ

## Q1：为什么我写了 `$skill_name` 不生效？

A：当前合法语法是 `$[namespace].skill_name`（见 `help/13-namespace-mentions.cn.md`）。`$skill_name` 属于 legacy 形态，自由文本里会被忽略。

## Q2：为什么不合法 mention 不报错？

A：这是有意的“对话不中断”策略：自由文本提取容错，不合法片段当普通文本处理。严格报错只在工具参数层生效。

## Q3：SDK 默认就开沙箱吗？

A：SDK 默认是 `sandbox.default_policy=none`（不强制 OS sandbox，避免改变既有行为）。

Studio MVP 的示例 overlay（`packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example`）默认把 `sandbox.default_policy` 设为 `restricted`，所以你用 MVP 跑起来时会更容易“体感到”沙箱相关约束。

想把“体感”变成可证据的验证，推荐跑一次：

```bash
bash scripts/integration/os_sandbox_restriction_demo.sh
```

## Q4：审批和沙箱到底有什么区别？

A：审批回答“让不让做”，沙箱回答“让做以后能做多远”。二者互补，不互相替代。

## Q5：`sandbox_denied` 出现时是不是会自动降级到非沙箱？

A：不会。当前语义是显式拒绝，避免“看似安全、实际裸跑”的静默降级。

## Q6：能不能把所有审批都关掉？

A：可以配 `safety.mode=allow`，但不建议。至少保留 denylist，否则会明显增加误操作风险。

## Q7：Studio run 卡住怎么排查？

A：先看 `events.jsonl` 是否持续产出；再看 pending approvals；最后检查 LLM/overlay/env 配置路径。

## Q8：如何确认某个配置字段来自哪里？

A：使用 bootstrap 的 `resolve_effective_run_config(...).sources` 做来源追踪。

## Q9：为什么我本机命令能跑，线上却失败？

A：常见原因是 Linux 上 `bwrap` 缺失、路径权限不同、或 overlay/env 差异。请按 `help/09-troubleshooting.md` 逐项核对。

## Q10：如果要做二次开发，先从哪里看？

A：推荐顺序：
1. `help/08-architecture-internals.md`
2. `help/06-tools-and-safety.md`（tools/approvals/sandbox 的心智模型与可观测字段）
3. 对应实现代码（`packages/skills-runtime-sdk-python/src/skills_runtime/*`，从 `skills_runtime/core/agent.py` 与 `skills_runtime/tools/builtin/*` 入手）

---

上一章：[`10-cookbook.cn.md`](./10-cookbook.cn.md)
