<div align="center">

[中文](14-safety-deep-dive.cn.md) | [English](14-safety-deep-dive.md) | [Help](README.cn.md)

</div>

# 14. Safety 深度解读：门卫 vs 围栏

本文档面向“需要落地的人”：解释如何把 Safety/Sandbox 做到 **足够安全**，同时又能兼顾 **开发体验** 与 **云端无人值守自动化**（不阻塞流水线、不无限等待人类点击）。

如果你只需要“参考手册”，优先读：
- `help/06-tools-and-safety.cn.md`（工具清单 + 配置入口）
- `help/sandbox-best-practices.cn.md`（平台差异 + 探测脚本）

如果你正在实现/审计安全姿态，本篇重点覆盖：
- *决策发生在哪里*（Guard → Policy → Approvals → Sandbox）
- *记录什么、脱敏什么*（可审计但不泄密）
- *兼容层如何保持同等门禁*（`shell_command`/`exec_command` 不能成为绕过点）
- *exec sessions 如何安全交互*（PTY + `write_stdin`）

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

### 14.1.1 心智模型：先“决定”，再“约束”

```text
门卫回答：要不要执行？
围栏回答：即使执行了，最多能碰到什么边界？

只门卫没围栏   → 一旦放行就是宿主机全能力
只围栏没门卫   → 各种莫名失败、解释成本高
两者都要        → 可解释 + 可审计 + 可约束
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

### 14.2.1 “sanitized request” 到底是什么意思？

脱敏是一份 **数据契约**：
- 记录足够的信息回答“我们批准/拒绝了什么”
- 不记录明文 secrets，也不把大 payload 直接塞进 WAL/UI

```text
原始 tool args（可能包含 secrets / 大文本）
   │
   ├─ sanitize → 小而稳定、可审计的结构化表示
   │
   ├─ hash     → approval_key（缓存 + loop guard）
   │
   └─ persist  → WAL 事件 + 审批 UI（不泄密）
```

### 记录与脱敏口径（示例）

框架对高风险工具的最小脱敏策略：

- `shell_exec`
  - 记录：`argv`、`cwd`、`timeout_ms`、`tty`、`sandbox`、`sandbox_permissions`、`risk`
  - env 只记录 `env_keys`（不记录 env values）
- `shell`（argv 形态）
  - 记录：`argv`（来自 `command` list）、`cwd`、`timeout_ms`、`tty`、`sandbox*`、`risk`
  - env 只记录 `env_keys`（不记录 env values）
- `shell_command` / `exec_command`（shell string 兼容层）
  - 记录原始 `command/cmd`（字符串）
  - 记录 `intent.argv`（对 shell string 的 best-effort 解析，用于 policy/审计）
  - 记录 `intent.is_complex` 与 `intent.reason`（为什么视为复杂命令）
  - env 只记录 `env_keys`（不记录 env values）
- `write_stdin`
  - 记录：`session_id`、`bytes`、`chars_sha256`、`is_poll`
  - 不记录明文 `chars`
- `file_write`
  - 记录：`path`、`create_dirs`、`sandbox_permissions`
  - 内容只记录 `bytes` + `content_sha256`（不落原文）
- `apply_patch`
  - best-effort 提取 `file_paths`
  - 内容只记录 `bytes` + `content_sha256`（不落原文）

### 14.2.2 为什么用 “hash 指纹” 而不是落明文？

这样你能同时获得：
- 可审计（批准了“这一次具体内容”）
- 可排障（内容变了 → hash 变了 → 触发新审批）
- 不泄密（WAL/UI 不出现 secrets 或大文本）

最小原则：
```text
审批侧：bytes + sha256
不要：原始 patch / 原始文件内容 / 原始 stdin 明文
```

实践铁律：
- **不要把 secrets 写进 argv。** 例如 `curl -H "Authorization: Bearer ..."` 会把 token 以明文进入审计与审批 UI。
- 应使用 env 注入（`.env` / tool args 的 `env`）+ shell 内展开。

### 14.2.3 脱敏 request 示例（JSON）

下列示例用于说明 **结构形态**（shape），不是逐字段承诺的稳定 API（以实现为准）。

关键点：
- `env` values 永远不落盘，只记录 `env_keys`
- stdin/patch/file 内容永远不以明文落盘，只记录 `bytes + sha256` 指纹

`shell_exec`（argv 形态）：

```json
{
  "argv": ["pytest", "-q"],
  "cwd": "/repo",
  "timeout_ms": 600000,
  "tty": false,
  "env_keys": ["OPENAI_API_KEY"],
  "sandbox": "restricted",
  "sandbox_permissions": null,
  "risk": { "risk_level": "low", "reason": "no risky patterns detected" }
}
```

`shell_command`（shell string 兼容层 + intent）：

```json
{
  "command": "pytest -q",
  "workdir": "/repo",
  "timeout_ms": 600000,
  "env_keys": [],
  "sandbox": "restricted",
  "sandbox_permissions": null,
  "intent": { "argv": ["pytest", "-q"], "is_complex": false, "reason": "parsed" },
  "risk": { "risk_level": "low", "reason": "no risky patterns detected" }
}
```

`exec_command`（PTY 会话入口；同样需要脱敏）：

```json
{
  "cmd": "python -i",
  "workdir": "/repo",
  "yield_time_ms": 1000,
  "max_output_tokens": 2000,
  "tty": true,
  "sandbox": "restricted",
  "sandbox_permissions": null,
  "intent": { "argv": ["python", "-i"], "is_complex": false, "reason": "parsed" },
  "risk": { "risk_level": "low", "reason": "no risky patterns detected" }
}
```

`write_stdin`（不得记录明文 `chars`）：

```json
{
  "session_id": 123,
  "yield_time_ms": 1000,
  "max_output_tokens": 2000,
  "bytes": 11,
  "chars_sha256": "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
  "is_poll": false
}
```

`skill_exec`（解析为底层动作；含 action 指纹）：

```json
{
  "skill_mention": "$[alice:engineering].python_testing",
  "action_id": "run_tests",
  "bundle_root": "/repo/skills/python_testing",
  "argv": ["bash", "actions/run_tests.sh"],
  "timeout_ms": 600000,
  "env_keys": ["OPENAI_API_KEY"],
  "resolve_error": null,
  "risk": { "risk_level": "low", "reason": "no risky patterns detected" },
  "action_sha256": "sha256-of-action-fingerprint"
}
```

### 14.2.4 端到端 WAL 事件流示例（JSONL）

下面给一段简化的 JSONL 时序，说明在 `safety.mode=ask` 下一个“需要 approvals”的工具调用会发生什么：

```text
tool_call_requested
  → approval_requested
  → approval_decided
  → tool_call_finished
  （→ 若触发 fail-fast / loop guard，会进一步产生 run_failed）
```

示例（未配置 `ApprovalProvider` → fail-fast）：

```jsonl
{"type":"tool_call_requested","timestamp":"2026-02-26T00:00:00Z","run_id":"run_123","turn_id":"turn_1","step_id":"step_1","payload":{"call_id":"c1","name":"shell_command","arguments":{"command":"pytest -q","workdir":"/repo","timeout_ms":600000,"env_keys":[],"sandbox":"restricted","sandbox_permissions":null,"intent":{"argv":["pytest","-q"],"is_complex":false,"reason":"parsed"},"risk":{"risk_level":"low","reason":"no risky patterns detected"}}}}
{"type":"approval_requested","timestamp":"2026-02-26T00:00:00Z","run_id":"run_123","turn_id":"turn_1","step_id":"step_1","payload":{"approval_key":"sha256(...)", "tool":"shell_command","summary":"(human-readable summary)","request":{"command":"pytest -q","workdir":"/repo","timeout_ms":600000,"env_keys":[],"sandbox":"restricted","sandbox_permissions":null,"intent":{"argv":["pytest","-q"],"is_complex":false,"reason":"parsed"},"risk":{"risk_level":"low","reason":"no risky patterns detected"}}}}
{"type":"approval_decided","timestamp":"2026-02-26T00:00:00Z","run_id":"run_123","turn_id":"turn_1","step_id":"step_1","payload":{"approval_key":"sha256(...)","decision":"denied","reason":"no_provider"}}
{"type":"tool_call_finished","timestamp":"2026-02-26T00:00:00Z","run_id":"run_123","turn_id":"turn_1","step_id":"step_1","payload":{"call_id":"c1","tool":"shell_command","result":{"ok":false,"stdout":"","stderr":"approval denied","duration_ms":0,"truncated":false,"data":{"tool":"shell_command"},"error_kind":"permission","retryable":false}}}
{"type":"run_failed","timestamp":"2026-02-26T00:00:00Z","run_id":"run_123","payload":{"error_kind":"config_error","message":"ApprovalProvider is required for tool 'shell_command' but none is configured.","retryable":false,"wal_locator":"<path>","details":{"tool":"shell_command","approval_key":"sha256(...)","reason":"no_provider"}}}
```

示例（`APPROVED_FOR_SESSION` 成功链路 + exec session）：

说明：
- `exec_command` 启动 PTY 会话，并在 tool result 的 `data` 中返回 `session_id`。
- 一旦该 session 在本 run 内被批准/启动，后续 `write_stdin(session_id=...)` 可免重复 ask（降噪）。
- `write_stdin` 的 approvals request 永远不记录明文 `chars`；即便跳过 approvals，`tool_call_requested.arguments` 仍是脱敏形态。

```jsonl
{"type":"tool_call_requested","timestamp":"2026-02-26T00:00:01Z","run_id":"run_456","turn_id":"turn_1","step_id":"step_1","payload":{"call_id":"c1","name":"exec_command","arguments":{"cmd":"python -i","workdir":"/repo","yield_time_ms":1000,"max_output_tokens":2000,"tty":true,"sandbox":"restricted","sandbox_permissions":null,"intent":{"argv":["python","-i"],"is_complex":false,"reason":"parsed"},"risk":{"risk_level":"low","reason":"no risky patterns detected"}}}}
{"type":"approval_requested","timestamp":"2026-02-26T00:00:01Z","run_id":"run_456","turn_id":"turn_1","step_id":"step_1","payload":{"approval_key":"sha256(...)", "tool":"exec_command","summary":"(human-readable summary)","request":{"cmd":"python -i","workdir":"/repo","yield_time_ms":1000,"max_output_tokens":2000,"tty":true,"sandbox":"restricted","sandbox_permissions":null,"intent":{"argv":["python","-i"],"is_complex":false,"reason":"parsed"},"risk":{"risk_level":"low","reason":"no risky patterns detected"}}}}
{"type":"approval_decided","timestamp":"2026-02-26T00:00:01Z","run_id":"run_456","turn_id":"turn_1","step_id":"step_1","payload":{"approval_key":"sha256(...)","decision":"approved_for_session","reason":"provider"}}
{"type":"tool_call_finished","timestamp":"2026-02-26T00:00:02Z","run_id":"run_456","turn_id":"turn_1","step_id":"step_1","payload":{"call_id":"c1","tool":"exec_command","result":{"ok":true,"stdout":"","stderr":"","exit_code":0,"duration_ms":120,"truncated":false,"data":{"session_id":123,"running":true},"retryable":false}}}
{"type":"tool_call_requested","timestamp":"2026-02-26T00:00:02Z","run_id":"run_456","turn_id":"turn_1","step_id":"step_2","payload":{"call_id":"c2","name":"write_stdin","arguments":{"session_id":123,"yield_time_ms":1000,"max_output_tokens":2000,"bytes":9,"chars_sha256":"sha256(...)","is_poll":false}}}
{"type":"tool_call_finished","timestamp":"2026-02-26T00:00:02Z","run_id":"run_456","turn_id":"turn_1","step_id":"step_2","payload":{"call_id":"c2","tool":"write_stdin","result":{"ok":true,"stdout":"Python 3.x ...\\n>>> ","stderr":"","exit_code":0,"duration_ms":80,"truncated":false,"data":{"session_id":123,"running":true},"retryable":false}}}
```

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

### 14.3.1 “命令执行路径”盘点（常见绕过点）

这些工具名不同，但必须共享同一安全姿态：

```text
shell_exec(argv=[...])          ┐
shell(command=[...])            │ 同一套 policy + approvals 语义
shell_command(command="...")    │（兼容层不得成为绕过点）
exec_command(cmd="...")         ┘

skill_exec(skill_mention, action_id)
  └─ 解析后本质是 shell 动作（必须按 shell_exec 同等门禁处理）
```

做安全审计时，务必验证 wrappers 的 parity。

## 14.4 Policy 决策树（denylist/allowlist/mode/risk）

Policy 是确定性的：同一份脱敏 request + 同一份配置，必须得到同一决策。

对 `shell_exec` 类路径，高层决策树如下：

```text
                +-------------------------------+
                | denylist 命中？                |
                +-------------------------------+
                      | 是 → DENY（不进 approvals）
                      v 否
                +-------------------------------+
                | safety.mode == deny ?         |
                +-------------------------------+
                      | 是 → DENY（不进 approvals）
                      v 否
                +-------------------------------+
                | sandbox_permissions 升权？     |
                +-------------------------------+
                      | 是 → ASK（必须审批）
                      v 否
                +-------------------------------+
                | allowlist 命中？               |
                +-------------------------------+
                      | 是 → ALLOW（不进 approvals）
                      v 否
                +-------------------------------+
                | safety.mode == allow ?        |
                +-------------------------------+
                      | 是 → ALLOW（不进 approvals）
                      v 否
                +-------------------------------+
                | 其它情况                      |
                +-------------------------------+
                      → ASK（需要 approvals）
```

说明：
- 在 `mode=ask` 下，allowlist 是“降噪/免弹窗”的主要手段（只对“单命令意图”可靠）。
- 在 `mode=deny` 下，命令执行类工具被框架级保守策略直接拒绝。

## 14.5 Shell wrappers：为什么要解析？为什么“复杂 shell”要特殊处理？

`shell_command` / `exec_command` 接受的是 *shell string*（例如 `"echo hi"`），但底层执行常常等价于 `/bin/sh -lc <command>`。

若我们直接把底层 wrapper argv 交给 allowlist/denylist：
```text
["/bin/sh", "-lc", "<command>"]
```
则规则永远只能匹配到 `/bin/sh`，而匹配不到真实意图（`echo`/`pytest`/`rg`…），policy 形同虚设。

### 14.5.1 intent argv（best-effort）

因此运行时会派生一个 **intent argv** 用于门禁与审计：

```text
command: "pytest -q"
intent.argv ≈ ["pytest", "-q"]
```

关键约束：
- intent 解析只用于“门禁/审计”，不用于执行（不能改变实际执行内容）
- 解析失败时应保守处理为复杂/高风险

### 14.5.2 复杂 shell string：在 mode=ask 下强制 approvals

以下字符串太“像 shell 程序”了，无法安全地靠 prefix allowlist 推断：

```text
pytest && rm -rf /
rg foo src | head -n 10
echo x > ~/.ssh/config
$(curl ...)
`cat secret.txt`
```

在 `safety.mode=ask` 下，这类模式建议即使 allowlist 命中也强制进入 approvals，
因为它可能组合多个动作或包含重定向。

经验法则：
- allowlist 面向“单命令意图”
- approvals 面向“shell 程序”

## 14.6 Exec sessions：PTY + write_stdin 如何安全落地

`exec_command` 可能启动长生命周期的 PTY 进程，返回 `session_id`；后续交互通过 `write_stdin(session_id=..., chars=...)` 完成。

### 14.6.1 典型时序图

```text
LLM
  │ tool_call: exec_command(cmd="python -i", tty=true)
  v
Runtime 门卫
  │（policy/approvals）
  v
Tool 执行 → 返回 {session_id, running=true}
  │
  │ tool_call: write_stdin(session_id, chars="print(1)\\n")
  v
Runtime 门卫
  │（policy/approvals；chars 明文不得落盘）
  v
Tool 写入 stdin → 返回 stdout/stderr 增量
```

### 14.6.2 审批体验：降噪但不 fail-open

推荐语义：
- 在 `mode=ask` 下，`write_stdin` 必须受门禁（交互式 session 能做任何事）。
- 若某 `session_id` 已在本 run 内通过审批/启动成功，后续对同一 session 的 `write_stdin` 可免重复 ask（降噪）。
- `write_stdin` 的审批请求不得记录明文 `chars`，只记录 `bytes + sha256`。

## 14.7 云端无人值守自动化（推荐模式）

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

## 14.8 开发体验：足够安全，但不过度打扰

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

### 14.8.1 最小配置草图（YAML overlay）

```yaml
safety:
  mode: ask
  allowlist:
    - "pytest"
    - "rg"
    - "cat"
  denylist:
    - "sudo"
    - "rm -rf"
```

刻意保持窄与可迁移；扩容应视为“安全审查事件”。

## 14.9 常见坑位（以及规避方法）

1) **secrets 写进 argv**
   - 不推荐：`curl -H "Authorization: Bearer <token>"`
   - 推荐：用 env 注入 + `$TOKEN` 展开

2) **误以为 requires_approval=true 就够了**
   - 真正的门禁必须在 Agent loop 里强制执行，而不是只在 `ToolSpec` 里“提示”。
   - parity wrapper（如 `shell_command`）不应成为绕过点。

3) **对 sandbox 的误解**
   - `sandbox=restricted` 是工具执行的 OS 隔离，不等于 SDK 本身的 LLM HTTP 调用也被隔离。
   - Linux bubblewrap 可以阻断 tool 进程的网络（如 `--unshare-net`），但 SDK 仍可在沙箱外请求 LLM。

4) **用 allowlist 去“相信”复杂 shell 程序**
   - `allowlist: ["pytest"]` 并不能让 `pytest && rm -rf /` 变安全。
   - 复杂 shell 应走 approvals（无人值守流水线建议直接拒绝或用更严格规则）。

## 14.10 真相来源（建议先看这些文件）

- Agent gate 编排：`packages/skills-runtime-sdk-python/src/skills_runtime/core/agent.py`
- policy/risk：`packages/skills-runtime-sdk-python/src/skills_runtime/safety/policy.py`、`.../guard.py`
- approvals 协议：`packages/skills-runtime-sdk-python/src/skills_runtime/safety/approvals.py`
- OS sandbox adapters：`packages/skills-runtime-sdk-python/src/skills_runtime/sandbox.py`
- 内置 exec tools：`packages/skills-runtime-sdk-python/src/skills_runtime/tools/builtin/`
