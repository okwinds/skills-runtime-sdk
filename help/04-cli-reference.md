<div align="center">

[English](04-cli-reference.md) | [中文](04-cli-reference.cn.md) | [Help](README.md)

</div>

# 04. CLI Reference: `skills-runtime-sdk` command set

## 4.1 Basic form

```bash
skills-runtime-sdk <command> <subcommand> [flags]
```

If you don't install the entrypoint, you can run via module:

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main <command> <subcommand> [flags]
```

## 4.2 Common flags (most subcommands)

- `--workspace-root`: workspace root directory (default `.`)
- `--config`: overlay YAML (repeatable)
- `--pretty`: pretty JSON output
- `--no-dotenv`: disable `.env` auto-load

## 4.3 Command groups

1. `skills`: config preflight and scan
2. `tools`: built-in tools (Codex parity)
3. `runs`: run metrics summaries

---

## 4.4 `skills` subcommands

### `skills preflight`

Purpose: validate skills config and availability (no real run execution).

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills preflight \
  --workspace-root . \
  --config help/examples/skills.cli.overlay.yaml \
  --pretty
```

### `skills scan`

Purpose: metadata-only scan for skills (does not read large bodies).

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills scan \
  --workspace-root . \
  --config help/examples/skills.cli.overlay.yaml \
  --pretty
```

---

## 4.5 `tools` subcommands (overview)

### Files and search

- `tools list-dir`
- `tools grep-files`
- `tools read-file`
- `tools apply-patch` (writes require `--yes`)

### Shell / Exec

- `tools shell`
- `tools shell-command`
- `tools exec-command`
- `tools write-stdin`

### Workflow / interaction

- `tools update-plan`
- `tools request-user-input`
- `tools view-image`
- `tools web-search` (disabled by default; requires provider)

### Multi-agent (collaboration)

- `tools spawn-agent`
- `tools wait`
- `tools send-input`
- `tools close-agent`
- `tools resume-agent`

---

## 4.6 Common examples

### Example 1: read file slice

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main tools read-file \
  --workspace-root . \
  --file-path help/README.md \
  --offset 1 \
  --limit 80 \
  --pretty
```

### Example 2: run shell (argv)

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main tools shell \
  --workspace-root . \
  --yes \
  --timeout-ms 30000 \
  --sandbox inherit \
  -- echo hello
```

### Example 3: PTY session

```bash
# start
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main tools exec-command \
  --workspace-root . \
  --yes \
  --cmd "python -u -c \"print('ready'); import time; time.sleep(2)\"" \
  --pretty

# then write
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main tools write-stdin \
  --workspace-root . \
  --yes \
  --session-id <id> \
  # PTY is often in canonical mode; CR is closer to pressing Enter.
  --chars $'hello\r' \
  --pretty
```

Notes:
- `exec-command` / `write-stdin` are backed by a workspace-local runtime service, so `session_id` works across multiple CLI invocations.
- Runtime artifacts live under `<workspace_root>/.skills_runtime_sdk/runtime/` (server info + logs). Socket may fallback to `/tmp/...sock` if the path would be too long.

---

## 4.7 `runs` subcommands

### `runs metrics`

Compute run metrics from `events.jsonl`.

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main runs metrics \
  --workspace-root . \
  --run-id <run_id> \
  --pretty
```

Or pass `--events-path` directly.

---

## 4.8 Exit codes (important)

### `skills`

- `0`: success
- `10/11/12`: config/scan errors or warnings

### `tools` (mapped from `error_kind`)

- `0`: ok
- `20`: validation
- `21`: permission
- `22`: not_found
- `23`: unknown
- `24`: sandbox_denied
- `25`: timeout
- `26`: human_required
- `27`: cancelled

## 4.9 Suggestions

- Run `skills preflight` before `skills scan`
- Writes require explicit `--yes`
- In CI, use `--pretty` and archive stdout JSON

---

Prev: [03. SDK Python API](03-sdk-python-api.md) · Next: [05. Skills Guide](05-skills-guide.md)
