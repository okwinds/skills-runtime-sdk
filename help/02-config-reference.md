<div align="center">

[English](02-config-reference.md) | [中文](02-config-reference.cn.md) | [Help](README.md)

</div>

# 02. Config Reference: From defaults to production policies

## 2.1 Config sources and precedence

An effective SDK runtime config can come from four layers (highest → lowest):

1. `session_settings` (product layer injection)
2. Environment variables (`SKILLS_RUNTIME_SDK_*`)
3. YAML overlays (`config/runtime.yaml` + `--config`)
4. Embedded defaults (SDK built-in defaults: `agent_sdk/assets/default.yaml`)

Where to find defaults:
- In this repo: `packages/skills-runtime-sdk-python/src/agent_sdk/assets/default.yaml`
- In an installed package: `agent_sdk/assets/default.yaml`

## 2.2 Default config highlights

- `run.max_steps=40`
- `run.max_wall_time_sec=1800`
- `safety.mode=ask`
- `sandbox.default_policy=none` (SDK default)
- `skills.scan.refresh_policy=always`
- `prompt.template=default`

## 2.3 Top-level fields

### `run`

- `max_steps`: maximum steps per run
- `max_wall_time_sec`: max wall-clock time per run
- `human_timeout_ms`: human input timeout (optional)
- `resume_strategy`: `summary|replay` (default: `summary`)
- `context_recovery`: context-length recovery (triggered on `context_length_exceeded`)
  - `context_recovery.mode`: `compact_first|ask_first|fail_fast` (default: `fail_fast`)
  - `context_recovery.max_compactions_per_run`: max compactions per run (prevents loops)
  - `context_recovery.ask_first_fallback_mode`: fallback when `ask_first` but no HumanIOProvider (`compact_first|fail_fast`)
  - `context_recovery.compaction_history_max_chars`: max chars for the compaction transcript input
  - `context_recovery.compaction_keep_last_messages`: keep last N user/assistant messages after compaction
  - `context_recovery.increase_budget_extra_steps`: extra steps when user chooses "increase budget"
  - `context_recovery.increase_budget_extra_wall_time_sec`: extra wall time seconds when user chooses "increase budget"

Notes:
- `compact_first` runs a compaction turn (tools disabled) to generate a handoff summary, rebuilds history, then retries.
- When compaction happens, terminal `run_completed.payload.metadata.notices[]` includes a prominent notice (not appended into `final_output`).

### `safety`

- `mode`: `allow|ask|deny`
- `allowlist`: command prefixes that can pass without approvals
- `denylist`: dangerous prefixes blocked early
- `tool_allowlist`: allowlisted custom tool names (exact match; reviewed tools can run unattended)
- `tool_denylist`: denylisted custom tool names (exact match; higher priority than allowlist)
- `approval_timeout_ms`: approval wait timeout

### `sandbox`

- `profile`: `custom|dev|balanced|prod` (macro for staged tightening)
  - `dev`: default does not enforce OS sandbox (availability-first)
  - `balanced`: recommended default (restricted + auto backend; Linux defaults to `unshare_net=true`)
  - `prod`: production-hardening baseline (tighten further via overlays)
  - `custom`: no macro expansion; behavior is driven purely by `default_policy/os.*`
- `default_policy`: `none|restricted`
- `os.mode`: `auto|none|seatbelt|bubblewrap`
- `os.seatbelt.profile`: macOS `sandbox-exec` profile
- `os.bubblewrap.*`: Linux `bwrap` params

### `llm`

- `base_url`
- `api_key_env`
- `timeout_sec`
- `retry`: retry/backoff policy (production-grade control)
  - `retry.max_retries`
  - `retry.base_delay_sec`: exponential backoff base (seconds; default `0.5`)
  - `retry.cap_delay_sec`: backoff cap (seconds; default `8.0`)
  - `retry.jitter_ratio`: jitter ratio (`0..1`; default `0.1`)

### `models`

- `planner`
- `executor`

### `skills`

- `spaces`: skill spaces (mention namespace)
- `sources`: skill sources (filesystem/redis/pgsql/in-memory)
- `env_var_missing_policy`: missing env var policy for skill dependencies: `ask_human|fail_fast|skip_skill` (default `ask_human`)
- `scan.*`: scan policy
- `injection.max_bytes`: injection budget
- `actions.enabled`: skills actions toggle
- `references.enabled`: restricted references toggle

### `prompt`

- `template`
- `system_text/developer_text`
- `system_path/developer_path`
- `include_skills_list`
- `history.max_messages / history.max_chars`

## 2.4 Dev-friendly recommended config (low interruption)

```yaml
config_version: 1

safety:
  mode: "ask"
  allowlist: ["ls", "pwd", "cat", "rg", "pytest"]
  denylist: ["sudo", "rm -rf", "shutdown", "reboot"]
  approval_timeout_ms: 60000

sandbox:
  profile: "balanced" # dev/balanced/prod/custom
  # profile expansion overrides default_policy/os.*; use overlays to fine-tune seatbelt/bwrap params
  default_policy: "restricted"
  os:
    mode: "auto"
    seatbelt:
      # Prefer multi-line seatbelt profiles for reviewability:
      profile: |
        (version 1)
        (allow default)

llm:
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"
```

## 2.5 Production recommendation (Linux)

```yaml
config_version: 1

safety:
  mode: "ask"
  allowlist: ["ls", "pwd", "cat", "rg"]
  denylist: ["sudo", "rm -rf", "mkfs", "dd", "shutdown", "reboot"]
  approval_timeout_ms: 60000

sandbox:
  profile: "prod"
  default_policy: "restricted"
  os:
    mode: "auto"
    bubblewrap:
      bwrap_path: "bwrap"
      unshare_net: true

run:
  max_steps: 40
  max_wall_time_sec: 1800
```

## 2.6 Common environment variables

- `SKILLS_RUNTIME_SDK_ENV_FILE`: env file path
- `SKILLS_RUNTIME_SDK_CONFIG_PATHS`: additional overlays (comma/semicolon separated)
- `SKILLS_RUNTIME_SDK_PLANNER_MODEL`
- `SKILLS_RUNTIME_SDK_EXECUTOR_MODEL`
- `SKILLS_RUNTIME_SDK_LLM_BASE_URL`
- `SKILLS_RUNTIME_SDK_LLM_API_KEY_ENV`

## 2.7 Overlay merge rules (must know)

- Deep-merge, last overlay wins
- Fixed discovery order:
  1) `<workspace_root>/config/runtime.yaml`
  2) `SKILLS_RUNTIME_SDK_CONFIG_PATHS`

## 2.8 Troubleshooting helpers

```bash
# Verify workspace and default overlay path
python3 - <<'PY'
from pathlib import Path
print(Path('.').resolve())
print((Path('.') / 'config' / 'runtime.yaml').resolve())
PY

# Preflight skills config
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills preflight --workspace-root . --config help/examples/skills.cli.overlay.yaml --pretty
```

## 2.9 Anti-patterns (avoid)

- Committing real API keys
- `safety.mode=allow` with an empty denylist
- Enabling `restricted` in prod without verifying `sandbox-exec`/`bwrap`
- Stacking too many overlays without source tracking

## 2.10 Further reading

- `help/06-tools-and-safety.md`
- `help/09-troubleshooting.md`

---

Prev: [01. Quickstart](01-quickstart.md) · Next: [03. SDK Python API](03-sdk-python-api.md)
