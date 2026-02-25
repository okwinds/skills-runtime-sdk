<div align="center">

[中文](14-safety-deep-dive.cn.md) | [English](14-safety-deep-dive.md) | [Help](README.cn.md)

</div>

# 14. Safety 深度解读：门卫 vs 围栏

本文档面向“需要落地的人”：解释如何把 Safety/Sandbox 做到 **足够安全**，同时又能兼顾 **开发体验** 与 **云端无人值守自动化**（不阻塞流水线、不无限等待人类点击）。

如果你只需要“参考手册”，优先读：
- `help/06-tools-and-safety.cn.md`（工具清单 + 配置入口）
- `help/sandbox-best-practices.cn.md`（平台差异 + 探测脚本）

## 14.1 两层模型（不可替代）

框架刻意把安全闭环拆成 **两层**：

- **门卫（Policy + Approvals）**：决定“要不要执行”。
- **围栏（OS sandbox）**：决定“允许执行后最多能执行到什么边界”（OS 级隔离）。

```text
ToolCall
  │
  ├─ Guard（风险识别）            → risk_level + reason
  │
  ├─ Policy（确定性门卫）         → allow | ask | deny
  │
  ├─ Approvals（人类/程序化审批）  → approved | approved_for_session | denied | abort
  │
  └─ OS Sandbox（OS 级隔离围栏）   → none | restricted
```

为什么必须两层都要：
- 只有门卫没有围栏：一旦放行就是“对宿主机的完整能力”，事故半径大。
- 只有围栏没有门卫：常见结果是“看起来允许执行，但各种莫名失败”，并且很难解释“为什么被阻断”。

## 14.2 approvals：可审计但不泄密

Approvals 的设计目标是：**可审计、可缓存**，但不把敏感信息以明文落盘或展示到 UI。

### approval_key

`approval_key = sha256(canonical_json(tool, sanitized_request))`

- canonical JSON 保证稳定（字段排序、结构固定），提升缓存命中率。
- 参与 hash 的 request 会先被 **脱敏**（sanitized），再写入 WAL/事件流。

### 记录与脱敏口径（示例）

框架对高风险工具的最小脱敏策略：

- `shell_exec`
  - 记录：`argv`、`cwd`、`timeout_ms`、`tty`、`sandbox`、`sandbox_permissions`、`risk`
  - env 只记录 `env_keys`（不记录 env values）
- `file_write`
  - 记录：`path`、`create_dirs`、`sandbox_permissions`
  - 内容只记录 `bytes` + `content_sha256`（不落原文）
- `apply_patch`
  - best-effort 提取 `file_paths`
  - 内容只记录 `bytes` + `content_sha256`（不落原文）

实践铁律：
- **不要把 secrets 写进 argv。** 例如 `curl -H "Authorization: Bearer ..."` 会把 token 以明文进入审计与审批 UI。
- 应使用 env 注入（`.env` / tool args 的 `env`）+ shell 内展开。

## 14.3 哪些工具走“门卫”？哪些工具走“围栏”？

### 围栏（OS sandbox）

OS sandbox 只对“真正执行命令”的工具生效，且仅在 `sandbox=restricted` 生效时启用：
- `shell_exec`
- `shell` / `shell_command`（内部是 `shell_exec` 的兼容层）
- `exec_command`（PTY session 入口，同样支持 sandbox wrapper）

若要求 `restricted` 但当前平台/配置无可用 adapter：
- MUST 返回 `error_kind="sandbox_denied"`
- MUST 不静默降级

### 门卫（policy + approvals）

最低要求：任何“命令执行路径”都 MUST 受同一套 policy/approvals 约束，不能通过别名/兼容工具绕过。

无人值守环境的关键约束：
- 若 `safety.mode=ask` 且某次工具调用需要 approvals，但未配置 `ApprovalProvider`，
  SDK MUST fail-fast（`run_failed(error_kind=config_error)`），避免模型在同一 run 内无限重试导致“看起来卡住”。

## 14.4 云端无人值守自动化（推荐模式）

目标：**不阻塞流水线**，同时 **不 fail-open**。

推荐做法：
- 保持 `safety.mode=ask`
- 注入“程序化审批”的 `ApprovalProvider`（规则匹配），默认 **DENIED**（fail-closed）

示例（只放行 `pytest`）：

```python
from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.safety.rule_approvals import ApprovalRule, RuleBasedApprovalProvider
from skills_runtime.safety.approvals import ApprovalDecision

provider = RuleBasedApprovalProvider(
    rules=[
        ApprovalRule(
            tool="shell_exec",
            condition=lambda req: (req.details.get("argv") or [None])[0] == "pytest",
            decision=ApprovalDecision.APPROVED,
        )
    ],
    default=ApprovalDecision.DENIED,
)

agent = Agent(workspace_root=Path(".").resolve(), backend=..., approval_provider=provider)
```

说明：
- 需要降低重复审批时，可使用 `approved_for_session`（同一 action 的 key 复用时免重复 ask）。
- 规则建议从窄到宽，扩容当作“安全审查事件”来处理。

## 14.5 开发体验：足够安全，但不过度打扰

主要杠杆：

- `safety.allowlist`：降低常用低风险命令的打扰（`rg`、`pytest`、`cat` 等）
- `safety.denylist`：对明显高危命令直接拒绝（`sudo`、`rm -rf`、`mkfs` 等）
- approvals cache（`approved_for_session`）：减少同一动作的重复弹窗
- `sandbox.default_policy` + profile 梯度：保留围栏，但不要把本地开发默认拉到“极严”

实用建议：
- 本地开发：`mode=ask` + 合理 allowlist + 最小可用的 sandbox profile
- 生产环境：`mode=ask`（或更严）+ 更严格的 sandbox profile + 保守 denylist

参考 Studio 的 overlay 示例：
- `packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example`

## 14.6 常见坑位（以及规避方法）

1) **secrets 写进 argv**
   - 不推荐：`curl -H "Authorization: Bearer <token>"`
   - 推荐：用 env 注入 + `$TOKEN` 展开

2) **误以为 requires_approval=true 就够了**
   - 真正的门禁必须在 Agent loop 里强制执行，而不是只在 `ToolSpec` 里“提示”。
   - parity wrapper（如 `shell_command`）不应成为绕过点。

3) **对 sandbox 的误解**
   - `sandbox=restricted` 是工具执行的 OS 隔离，不等于 SDK 本身的 LLM HTTP 调用也被隔离。
   - Linux bubblewrap 可以阻断 tool 进程的网络（如 `--unshare-net`），但 SDK 仍可在沙箱外请求 LLM。

## 14.7 真相来源（建议先看这些文件）

- Agent gate 编排：`packages/skills-runtime-sdk-python/src/skills_runtime/core/agent.py`
- policy/risk：`packages/skills-runtime-sdk-python/src/skills_runtime/safety/policy.py`、`.../guard.py`
- approvals 协议：`packages/skills-runtime-sdk-python/src/skills_runtime/safety/approvals.py`
- OS sandbox adapters：`packages/skills-runtime-sdk-python/src/skills_runtime/sandbox.py`
- 内置 exec tools：`packages/skills-runtime-sdk-python/src/skills_runtime/tools/builtin/`

