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
- 源码入口：`packages/skills-runtime-sdk-python/src/agent_sdk/tools/*`、`packages/skills-runtime-sdk-python/src/agent_sdk/safety/*`、`packages/skills-runtime-sdk-python/src/agent_sdk/sandbox.py`

---

上一章：[05. Skills 指南](05-skills-guide.cn.md) · 下一章：[07. Studio 指南](07-studio-guide.cn.md)
