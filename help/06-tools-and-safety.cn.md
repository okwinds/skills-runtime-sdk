<div align="center">

[中文](06-tools-and-safety.cn.md) | [English](06-tools-and-safety.md) | [Help](README.cn.md)

</div>

# 06. Tools + Safety：工具执行与安全治理

## 6.1 全局视角

一次 tool 调用的典型链路：

1. LLM 产生 `tool_call`
2. 框架计算 `approval_key` 并进入 policy gate
3. 命中 denylist 直接拒绝；命中 allowlist 可直通；否则走 approvals
4. 若 sandbox=`restricted`，进入 OS sandbox 适配器
5. 执行工具并写入 `tool_call_finished`
6. 将结果回注给 LLM

## 6.2 内置工具清单（当前）

- 执行类：`shell_exec` / `shell` / `shell_command` / `exec_command` / `write_stdin`
- 文件类：`file_read` / `file_write` / `read_file` / `list_dir` / `grep_files` / `apply_patch`
- 交互类：`ask_human` / `request_user_input` / `update_plan`
- Skills 类：`skill_exec` / `skill_ref_read`
- 其他：`view_image` / `web_search`
- 协作：`spawn_agent` / `wait` / `send_input` / `close_agent` / `resume_agent`

## 6.3 Approval 策略（门卫）

配置入口：

```yaml
safety:
  mode: "ask"
  allowlist: ["ls", "pwd", "cat", "rg"]
  denylist: ["sudo", "rm -rf", "shutdown", "reboot"]
  tool_allowlist: []          # 自定义工具白名单（精确匹配 tool name）
  tool_denylist: []           # 自定义工具黑名单（精确匹配 tool name）
  approval_timeout_ms: 60000
```

含义：
- `mode=ask`：默认审批
- allowlist：降低高频安全操作的打扰
- denylist：高危动作前置拒绝
- `tool_allowlist/tool_denylist`：用于自定义工具（非 builtin tools）的无人值守治理：默认 `ask`，只有显式 allowlist 才可免审批执行；denylist 强制拒绝。

### 6.3.1 程序化规则审批（云端无人值守推荐）

“无人值守”不是“不要审批”，而是：

- 运行过程中不依赖人类点击
- 审批决策由 **代码** 自动给出（可回归的规则），并且默认 **fail-closed**
- 仍然可审计（事件/WAL），但不会把 secrets 明文写进日志或 UI

当你没有“人类点击确认”的交互入口时，推荐注入规则审批 Provider（默认 fail-closed：未命中规则一律拒绝）：

```python
from pathlib import Path
from skills_runtime.agent import Agent
from skills_runtime.safety import ApprovalRule, RuleBasedApprovalProvider
from skills_runtime.safety.approvals import ApprovalDecision

provider = RuleBasedApprovalProvider(
    rules=[
        # 示例：仅放行 `shell_exec` 且 argv[0] 为 pytest 的情况（其它一律拒绝）
        ApprovalRule(
            tool="shell_exec",
            condition=lambda req: (req.details.get("argv") or [None])[0] == "pytest",
            decision=ApprovalDecision.APPROVED,
        )
    ],
    default=ApprovalDecision.DENIED,
)

agent = Agent(
    workspace_root=Path(".").resolve(),
    backend=...,  # 你的 LLM backend
    approval_provider=provider,
)
```

说明：
- ApprovalRequest 由 SDK 生成并已做最小脱敏（不包含 env values、file_write content 明文等）。
- `condition` 抛异常时会被视为“不命中”（fail-closed）。

#### 6.3.1.1 框架到底做了什么（原理 + 流程 + 事件）

把 approvals 想成“危险执行路径前的硬门卫”，并且是可审计的：

```text
LLM tool_call
  │
  ├─ sanitize request（WAL/UI 不落 secrets 明文）
  │
  ├─ approval_key = sha256(canonical_json(tool, sanitized_request))
  │
  ├─ policy gate（denylist / allowlist / safety.mode）
  │
  ├─ ApprovalProvider.request_approval(...)（UI 点击 或 规则自动决策）
  │
  ├─ cache?（approved_for_session）
  │
  └─ dispatch tool -> tool_call_finished
```

WAL/SSE 时间线上会出现稳定的序列：

```text
tool_call_requested
approval_requested
approval_decided（approved/denied/approved_for_session/abort）
tool_call_started      （只有 approved 才会出现）
tool_call_finished
```

这套结构同时给你：
- **安全性**：任何危险路径都不能绕过门卫
- **体验价值**：allowlist + 缓存减少打扰，但仍有清晰审计链

更深入的“脱敏契约 / approval_key / 示例事件序列”，见：
- `help/14-safety-deep-dive.cn.md`

#### 6.3.1.2 `approved_for_session`：不削弱安全的 UX 放大器

有些操作在交互场景会很“吵”（例如 `exec_command` 打开一个交互式 session 后，需要多次 `write_stdin`）。

当决策是 `approved_for_session` 时，SDK 会把该 `approval_key` 缓存在当前 run/session 内：
后续相同动作可以跳过反复弹审批，但工具事件仍以“脱敏形态”进入 WAL。

```text
第一次： approval_requested -> approved_for_session -> 工具执行
后续：  approval_key 命中缓存 -> 跳过 approval_requested -> 工具执行
```

典型受益场景：
- PTY/exec sessions（`exec_command` + `write_stdin`）
- 轮询型 workflow（反复读状态/输出）

#### 6.3.1.3 既安全又好用的规则设计

原则：
- 优先用 **argv 形态** 的工具（`shell_exec`/`shell`），尽量避免直接放行 shell string（`shell_command`/`exec_command`）。
  - shell string 可能包含管道/重定向/`&&` 等；在 `safety.mode=ask` 下，“复杂 shell”会被视为需要 approvals，即便 allowlist 命中也不应静默放行。
- 只放行窄而明确的低风险操作，其它全部拒绝（fail-closed）。
- allowlist 保持短、可审阅；“上下文相关”的放行用规则表达。

示例：只允许跑测试（`pytest`/`python`），并对交互式 `write_stdin` 用 session 级缓存降低噪音：

```python
from skills_runtime.safety import ApprovalRule, RuleBasedApprovalProvider
from skills_runtime.safety.approvals import ApprovalDecision

def _argv0(req):
    return (req.details.get("argv") or [None])[0]

provider = RuleBasedApprovalProvider(
    rules=[
        ApprovalRule(
            tool="shell_exec",
            condition=lambda req: _argv0(req) in ("pytest", "python"),
            decision=ApprovalDecision.APPROVED,
        ),
        ApprovalRule(
            tool="write_stdin",
            condition=lambda req: True,
            decision=ApprovalDecision.APPROVED_FOR_SESSION,
        ),
    ],
    default=ApprovalDecision.DENIED,
)
```

### 6.3.2 面向生产的无人值守配置配方

无人值守自动化的目标通常是：
- 不把人类放在关键路径上
- 遇到不安全/不确定动作时“快速、可解释地失败”
- 既减少噪音，也保留足够审计证据

#### 配方 A：CI 里“默认安全”的运行基线

```yaml
config_version: 1

safety:
  mode: "ask"
  # 保持短：高频且低风险。
  allowlist: ["pwd", "ls", "cat", "rg", "pytest"]
  # 先把明显的脚枪堵死。
  denylist: ["sudo", "rm -rf", "mkfs", "dd", "shutdown", "reboot"]
  # 如果 ApprovalProvider 是远端实现（HTTP/队列），建议把等待时间收敛到可控范围。
  approval_timeout_ms: 5000

# 可选但推荐：用 OS sandbox 做围栏（降低事故半径）。
sandbox:
  default_policy: "restricted"
  os:
    mode: "auto"

run:
  # 若存在任何人类输入通道，也应把等待时间限定住。
  human_timeout_ms: 3000
```

#### 配方 B：严格“无人工介入”的流水线

如果一个 job 绝对不能等人类：依然建议使用 `safety.mode=ask`，但把 ApprovalProvider 设计成确定性的、fail-closed 的规则决策。
这样“未覆盖的动作”会变成干净的拒绝（并带解释），而不是 run 卡住。

### 6.3.3 这如何带来用户体验价值

对开发者/用户来说，“好的安全治理”不仅是拦截，更是可预测性：

- 少打扰：allowlist + `approved_for_session` 缓存
- 好解释：policy 拒绝 / approvals 拒绝 / sandbox 拒绝在事件流里口径清晰
- 可审计但不泄密：脱敏 request 小而稳定，适合生产日志与审计

## 6.4 Sandbox 策略（围栏）

### SDK 默认

- `sandbox.default_policy=none`

### Studio MVP 当前

- 已使用平衡模式：`default_policy=restricted` + `os.mode=auto`

### 平台映射

- macOS：seatbelt（`sandbox-exec`）
- Linux：bubblewrap（`bwrap`）

## 6.5 `sandbox` 参数语义

工具侧可显式传：
- `inherit`
- `none`
- `restricted`

语义：
- `inherit`：沿用默认策略
- `none`：本次不使用 OS sandbox
- `restricted`：强制沙箱执行

若要求 `restricted` 但适配器不可用：返回 `sandbox_denied`。

## 6.6 Exec Sessions（PTY）

适用：长任务、交互式命令。

最小流程：
1. `exec_command` 启动，拿 `session_id`
2. `write_stdin` 写入输入/轮询输出
3. 子进程结束后 session 自动清理

## 6.7 典型调用示例

### `shell_exec`

```json
{
  "argv": ["bash", "-lc", "pytest -q"],
  "cwd": ".",
  "timeout_ms": 120000,
  "sandbox": "inherit"
}
```

### `exec_command`

```json
{
  "cmd": "python -u -c \"print('ready'); import time; time.sleep(3)\"",
  "yield_time_ms": 50,
  "sandbox": "inherit"
}
```

### `write_stdin`

```json
{
  "session_id": 1,
  "chars": "hello\n",
  "yield_time_ms": 200
}
```

## 6.8 错误码速查

- `validation`
- `permission`
- `not_found`
- `sandbox_denied`
- `timeout`
- `human_required`
- `cancelled`
- `unknown`

## 6.9 安全基线建议

1. 开发环境：`ask + allowlist + denylist + restricted(最小 profile)`
2. 生产环境：逐步收紧 allowlist 与 profile，不一次性拉满
3. 把 `sandbox_denied` 视为配置问题，不是“自动降级可接受”

## 6.10 相关阅读

- `help/sandbox-best-practices.cn.md`
- `help/04-cli-reference.cn.md`
- `help/09-troubleshooting.cn.md`
- 源码入口：`packages/skills-runtime-sdk-python/src/skills_runtime/tools/*`、`packages/skills-runtime-sdk-python/src/skills_runtime/safety/*`、`packages/skills-runtime-sdk-python/src/skills_runtime/sandbox.py`

---

上一章：[05. Skills 指南](05-skills-guide.cn.md) · 下一章：[07. Studio 指南](07-studio-guide.cn.md)
