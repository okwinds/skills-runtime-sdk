<div align="center">

[中文](02-config-reference.cn.md) | [English](02-config-reference.md) | [Help](README.cn.md)

</div>

# 02. 配置参考：从默认值到生产策略

## 2.1 配置来源与优先级

SDK 运行时有效配置可来自四层（高到低）：

1. `session_settings`（产品层动态注入）
2. 环境变量（`SKILLS_RUNTIME_SDK_*`）
3. overlay YAML（`config/runtime.yaml` + `--config`）
4. embedded default（SDK 内置默认配置：`skills_runtime/assets/default.yaml`）

重要说明（当前行为）：
- `session_settings` 是**白名单覆盖**，不是任意字段的动态注入。
- 目前仅覆盖：
  - `models.planner` / `models.executor`
  - `llm.base_url` / `llm.api_key_env`
- 其它字段请通过环境变量或 YAML overlays 配置。

## 2.2 默认配置（关键字段）

参考：
- 源码仓库内：`packages/skills-runtime-sdk-python/src/skills_runtime/assets/default.yaml`
- 安装包内：`skills_runtime/assets/default.yaml`

- `run.max_steps=40`
- `run.max_wall_time_sec=1800`
- `safety.mode=ask`
- `sandbox.default_policy=none`（SDK 缺省）
- `skills.scan.refresh_policy=always`
- `prompt.template=default`

## 2.3 顶层字段说明

### `run`

- `max_steps`：单次 run 最大 step 数
- `max_wall_time_sec`：单次 run 最大墙钟时间
- `human_timeout_ms`：人类输入超时（可空）
- `resume_strategy`：`summary|replay`（默认 `summary`；`replay` 为逐事件回放恢复）
- `context_recovery`：上下文恢复策略（当 LLM 返回 `context_length_exceeded` 时触发）
  - `context_recovery.mode`：`compact_first|ask_first|fail_fast`（默认 `fail_fast`）
  - `context_recovery.max_compactions_per_run`：单个 run 最大压缩次数（防无限循环）
  - `context_recovery.ask_first_fallback_mode`：`ask_first` 但无 HumanIOProvider 时的降级策略（`compact_first|fail_fast`）
  - `context_recovery.compaction_history_max_chars`：compaction turn 输入“对话节选”字符上限
  - `context_recovery.compaction_keep_last_messages`：压缩后保留最近 user/assistant 原文条数（其余由摘要承载）
  - `context_recovery.increase_budget_extra_steps`：用户选择“提高预算继续”时增加的 step 数
  - `context_recovery.increase_budget_extra_wall_time_sec`：用户选择“提高预算继续”时增加的 wall time 秒数

说明：
- `compact_first` 会触发一次 compaction turn（tools 禁用）生成 handoff 摘要，并用摘要重建 history 后重试采样。
- compaction 发生时，终态 `run_completed.payload.metadata.notices[]` 会携带明显提示（不拼进 `final_output` 正文）。

### `safety`

- `mode`：`allow|ask|deny`
- `allowlist`：允许直通的命令前缀
- `denylist`：直接拒绝的高危命令前缀
- `tool_allowlist`：自定义工具白名单（精确匹配 tool name；无人值守场景用于“显式认可”免审批）
- `tool_denylist`：自定义工具黑名单（精确匹配 tool name；优先级高于 allowlist）
- `approval_timeout_ms`：审批等待超时

### `sandbox`

- `profile`：`dev|balanced|prod`（高层宏；用于分阶段收紧）
  - `dev`：默认不强制 OS sandbox（可用性优先）
  - `balanced`：推荐默认（restricted + auto backend；Linux 默认隔离网络）
  - `prod`：更偏生产硬化（提供更严格的基线；建议结合 overlay 按业务调整）
- `default_policy`：`none|restricted`
- `os.mode`：`auto|none|seatbelt|bubblewrap`
- `os.seatbelt.profile`：macOS sandbox-exec profile
- `os.bubblewrap.*`：Linux bwrap 参数

### `llm`

- `base_url`
- `api_key_env`
- `timeout_sec`
- `retry`：重试/退避策略（生产级可控）
  - `retry.max_retries`
  - `retry.base_delay_sec`：指数退避基线（秒；默认 `0.5`）
  - `retry.cap_delay_sec`：退避上限（秒；默认 `8.0`）
  - `retry.jitter_ratio`：抖动比例（`0..1`；默认 `0.1`）

### `models`

- `planner`
- `executor`

### `skills`

- `spaces`：skill 空间（mention 命名空间）
- `sources`：skill 来源（filesystem/redis/pgsql/in-memory）
- `env_var_missing_policy`：skill 依赖 env var 缺失策略：`ask_human|fail_fast|skip_skill`（默认 `ask_human`）
- `scan.*`：扫描策略
- `injection.max_bytes`：注入上限
- `bundles.*`：bundle 预算与缓存（Phase 3：actions/references；例如 Redis bundles）
- `actions.enabled`：Skills actions 开关
- `references.enabled`：受限引用开关

#### `skills.bundles`（Phase 3 bundles：actions / references）

用于 bundle-backed 的 Phase 3 工具路径（例如 Redis bundles）的预算与缓存策略：

- `skills.bundles.max_bytes`：bundle bytes 最大值（默认 `1048576`，即 1 MiB；超限 fail-closed）
- `skills.bundles.cache_dir`：bundle 解压缓存目录（默认 `.skills_runtime_sdk/bundles`；runtime-owned，可安全删除并重建）
- `skills.bundles.max_extracted_bytes`：解压后总字节预算（默认 `null`：由运行时按 `max_bytes * 16` 推导；超限 fail-closed）
- `skills.bundles.max_files`：解压文件数预算（默认 `null`：运行时默认 `4096`；超限 fail-closed）
- `skills.bundles.max_single_file_bytes`：单文件解压字节预算（默认 `null`：由运行时按 `max_bytes * 8` 推导；超限 fail-closed）

推荐值（以默认 `max_bytes=1MiB` 为例）：
- `max_extracted_bytes: 16777216`（16 MiB）
- `max_single_file_bytes: 8388608`（8 MiB）
- `max_files: 4096`

### `prompt`

- `profile`：`default_agent|generation_direct|structured_transform`
  - `default_agent` 保持 coding/tool agent 基线：默认注入 developer policy、skills list、history，并向 provider 暴露当前已启用/已注册的 provider tools。
  - `generation_direct` 面向低噪音生产文本生成：默认不注入 coding/TDD developer policy、不注入全量 skills list、只注入显式 mention 的 skill、不注入 history、不向 provider 暴露 tools。
  - `structured_transform` 面向抽取/转换任务：默认不注入全量 skills list，显式 skill 以 summary 形态注入，不注入 history，不向 provider 暴露 tools。
- `template`
- `system_text/developer_text`
- `system_path/developer_path`
- 当 `template: default` 且没有设置 `system_text`、`developer_text`、`system_path`、`developer_path` 时，`generation_direct` 与 `structured_transform` 会自动使用同名内置 prompt 模板，而不是继续使用 default agent 模板。
- `include_skills_list`：`null` 表示使用 profile 默认值；写 `true|false` 可显式覆盖。
- `skill_injection.mode`：`all|explicit_only|none`
- `skill_injection.render`：`body|method_only|summary|none`
- `history.mode`：`none|compacted|full`；`null` 表示使用 profile 默认值。
- 当前版本中，`history.mode: compacted` 是预留模式，运行时行为与 `full` 相同：两者都走 `max_messages` / `max_chars` 滑窗裁剪路径，但 debug 元数据会保留所选模式。自动摘要压缩可在后续版本接入，而不需要改变配置形态。
- `history.max_messages / history.max_chars`
- `tools.exposure`：`none|explicit_only|all`；该字段控制 provider `tools[]`，不只是 prompt 文本。`explicit_only` 按当前请求文本（`task` + `user_input`）中的精确注册 tool name 匹配，例如 `file_read`；“读取文件”这类自然语言不会自动暴露工具。

低噪音生成 agent 示例：

```yaml
config_version: 1

prompt:
  profile: "generation_direct"
  system_text: "Write polished customer-facing copy that follows the requested brief."
  developer_text: ""
  # 以下字段已是 generation_direct 默认值，仅在需要显式覆盖时填写：
  include_skills_list: false
  skill_injection:
    mode: "explicit_only"
    render: "body"
  history:
    mode: "none"
  tools:
    exposure: "none"
```

当宿主应用需要 direct generation，而不是 agentic/tool 行为时，使用 `generation_direct`。
coding/repo agent 仍使用 `default_agent`，保留 tools、history 与 available-skills list。

只要设置了 `system_text`、`developer_text`、`system_path`、`developer_path` 中任一字段，prompt 模板就视为显式配置。此时不再触发 profile 同名内置模板自动选择；profile 仍会控制 skills list、skill 注入、history 和 tools 暴露。

结构化转换 agent 示例：

```yaml
config_version: 1

prompt:
  profile: "structured_transform"
  # 可省略 system/developer 字段，直接使用内置 structured_transform 模板。
  history:
    mode: "none"
  tools:
    exposure: "none"
```

## 2.4 开发环境推荐配置（低打扰）

```yaml
config_version: 1

safety:
  mode: "ask"
  allowlist: ["ls", "pwd", "cat", "rg", "pytest"]
  denylist: ["sudo", "rm -rf", "shutdown", "reboot"]
  approval_timeout_ms: 60000

sandbox:
  profile: "balanced" # dev/balanced/prod
  # profile 提供基线默认值；显式字段可覆盖（用于精细化 seatbelt/bwrap 参数）
  default_policy: "restricted"
  os:
    mode: "auto"
    seatbelt:
      # 建议用多行文本写 seatbelt profile（便于审阅与演进）：
      profile: |
        (version 1)
        (allow default)

llm:
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"
```

## 2.5 生产环境建议（Linux）

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

## 2.6 常见环境变量

- `SKILLS_RUNTIME_SDK_ENV_FILE`：指定 `.env` 文件路径
- `SKILLS_RUNTIME_SDK_CONFIG_PATHS`：追加 overlay（逗号/分号分隔）
- `SKILLS_RUNTIME_SDK_PLANNER_MODEL`
- `SKILLS_RUNTIME_SDK_EXECUTOR_MODEL`
- `SKILLS_RUNTIME_SDK_LLM_BASE_URL`
- `SKILLS_RUNTIME_SDK_LLM_API_KEY_ENV`

## 2.7 overlay 合并规则（必须记住）

- 采用“深度合并 + 后者覆盖前者”
- 路径发现顺序固定：
  1) `<workspace_root>/config/runtime.yaml`
  2) `SKILLS_RUNTIME_SDK_CONFIG_PATHS`

## 2.8 配置排障命令

```bash
# 检查 workspace 与 overlay 路径
python3 - <<'PY'
from pathlib import Path
print(Path('.').resolve())
print((Path('.') / 'config' / 'runtime.yaml').resolve())
PY

# preflight 校验 skills 配置
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m skills_runtime.cli.main skills preflight --workspace-root . --config help/examples/skills.cli.overlay.yaml --pretty
```

## 2.9 反例（不要这样配）

- 在仓库里提交真实 API key
- `safety.mode=allow` 且 denylist 为空
- 生产启用 `restricted` 但不验证 `bwrap/sandbox-exec` 可用
- 混用过多 overlay 且无来源追踪

## 2.10 相关阅读

- `help/06-tools-and-safety.cn.md`
- `help/09-troubleshooting.cn.md`

---

上一章：[01. Quickstart](01-quickstart.cn.md) · 下一章：[03. SDK Python API](03-sdk-python-api.cn.md)
