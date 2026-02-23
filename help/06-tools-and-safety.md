<div align="center">

[English](06-tools-and-safety.md) | [中文](06-tools-and-safety.cn.md) | [Help](README.md)

</div>

# 06. Tools + Safety: execution and governance

## 6.1 End-to-end flow

A typical tool call flow:

1. LLM emits a `tool_call`
2. The framework computes an `approval_key` and enters the policy gate
3. denylist blocks early; allowlist may pass-through; otherwise approvals are requested
4. If `sandbox=restricted`, the OS sandbox adapter is applied
5. Execute the tool and emit `tool_call_finished`
6. Feed the result back to the LLM

## 6.2 Built-in tools (current)

- Exec: `shell_exec` / `shell` / `shell_command` / `exec_command` / `write_stdin`
- Files: `file_read` / `file_write` / `read_file` / `list_dir` / `grep_files` / `apply_patch`
- Interaction: `ask_human` / `request_user_input` / `update_plan`
- Skills: `skill_exec` / `skill_ref_read`
- Other: `view_image` / `web_search`
- Collaboration: `spawn_agent` / `wait` / `send_input` / `close_agent` / `resume_agent`

## 6.3 Approvals policy (gatekeeper)

Config entry:

```yaml
safety:
  mode: "ask"
  allowlist: ["ls", "pwd", "cat", "rg"]
  denylist: ["sudo", "rm -rf", "shutdown", "reboot"]
  tool_allowlist: []          # custom tools allowlist (exact tool name match)
  tool_denylist: []           # custom tools denylist (exact tool name match)
  approval_timeout_ms: 60000
```

Meaning:
- `mode=ask`: approvals by default
- allowlist: reduce interruptions for safe frequent commands
- denylist: block dangerous operations early
- `tool_allowlist/tool_denylist`: governs custom tools (non-builtin) for unattended runs: default is `ask`; only allowlisted tools can run without approvals; denylist blocks hard.

## 6.4 Sandbox policy (fence)

### SDK default

- `sandbox.default_policy=none`

### Studio MVP baseline

- balanced: `default_policy=restricted` + `os.mode=auto`

### Platform mapping

- macOS: seatbelt (`sandbox-exec`)
- Linux: bubblewrap (`bwrap`)

## 6.5 `sandbox` parameter semantics

Tools may explicitly pass:

- `inherit`
- `none`
- `restricted`

Meaning:
- `inherit`: follow default policy
- `none`: do not use OS sandbox for this call
- `restricted`: force OS sandbox

If `restricted` is required but the adapter is unavailable: `sandbox_denied` (no silent fallback).

## 6.6 Exec Sessions (PTY)

Use for long-running or interactive commands:

1. `exec_command` starts and returns `session_id`
2. `write_stdin` sends input / polls output
3. session is cleaned up after process exits

## 6.7 Typical examples

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

## 6.8 Error kinds (quick lookup)

- `validation`
- `permission`
- `not_found`
- `sandbox_denied`
- `timeout`
- `human_required`
- `cancelled`
- `unknown`

## 6.9 Baseline recommendations

1. Dev: `ask + allowlist + denylist + restricted (minimal profile)`
2. Prod: tighten allowlist and sandbox profile gradually
3. Treat `sandbox_denied` as a config/environment problem (not an acceptable silent fallback)

## 6.10 Further reading

- `help/sandbox-best-practices.md`
- `help/04-cli-reference.md`
- `help/09-troubleshooting.md`
- Code entrypoints: `packages/skills-runtime-sdk-python/src/agent_sdk/tools/*`, `packages/skills-runtime-sdk-python/src/agent_sdk/safety/*`, `packages/skills-runtime-sdk-python/src/agent_sdk/sandbox.py`

---

Prev: [05. Skills Guide](05-skills-guide.md) · Next: [07. Studio Guide](07-studio-guide.md)
