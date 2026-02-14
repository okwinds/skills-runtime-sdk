<div align="center">

[中文](02-config-reference.cn.md) | [English](02-config-reference.md) | [Help](README.cn.md)

</div>

# 02. 配置参考：从默认值到生产策略

## 2.1 配置来源与优先级

SDK 运行时有效配置可来自四层（高到低）：

1. `session_settings`（产品层动态注入）
2. 环境变量（`SKILLS_RUNTIME_SDK_*`，兼容 `AGENT_SDK_*`）
3. overlay YAML（`config/runtime.yaml` + `--config`）
4. embedded default（SDK 内置默认配置：`agent_sdk/assets/default.yaml`）

## 2.2 默认配置（关键字段）

参考：
- 源码仓库内：`packages/skills-runtime-sdk-python/src/agent_sdk/assets/default.yaml`
- 安装包内：`agent_sdk/assets/default.yaml`

- `run.max_steps=40`
- `run.max_wall_time_sec=1800`
- `safety.mode=ask`
- `sandbox.default_policy=none`（SDK 缺省）
- `skills.mode=explicit`
- `skills.scan.refresh_policy=always`
- `prompt.template=default`

## 2.3 顶层字段说明

### `run`

- `max_steps`：单次 run 最大 step 数
- `max_wall_time_sec`：单次 run 最大墙钟时间
- `human_timeout_ms`：人类输入超时（可空）
- `resume_strategy`：`summary|replay`（默认 `summary`；`replay` 为逐事件回放恢复）

### `safety`

- `mode`：`allow|ask|deny`
- `allowlist`：允许直通的命令前缀
- `denylist`：直接拒绝的高危命令前缀
- `approval_timeout_ms`：审批等待超时

### `sandbox`

- `default_policy`：`none|restricted`
- `os.mode`：`auto|none|seatbelt|bubblewrap`
- `os.seatbelt.profile`：macOS sandbox-exec profile
- `os.bubblewrap.*`：Linux bwrap 参数

### `llm`

- `base_url`
- `api_key_env`
- `timeout_sec`
- `max_retries`

### `models`

- `planner`
- `executor`

### `skills`

- `mode`：`explicit`（当前要求）
- `scan.*`：扫描策略
- `injection.max_bytes`：注入上限
- `actions.enabled`：Skills actions 开关
- `references.enabled`：受限引用开关

### `prompt`

- `template`
- `system_text/developer_text`
- `system_path/developer_path`
- `include_skills_list`
- `history.max_messages / history.max_chars`

## 2.4 开发环境推荐配置（低打扰）

```yaml
config_version: 1

safety:
  mode: "ask"
  allowlist: ["ls", "pwd", "cat", "rg", "pytest"]
  denylist: ["sudo", "rm -rf", "shutdown", "reboot"]
  approval_timeout_ms: 60000

sandbox:
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

兼容旧前缀：`AGENT_SDK_*`。

## 2.7 overlay 合并规则（必须记住）

- 采用“深度合并 + 后者覆盖前者”
- 路径发现顺序固定：
  1) `<workspace_root>/config/runtime.yaml`
  2) 若 `runtime.yaml` 不存在且 `<workspace_root>/config/llm.yaml` 存在，则作为 legacy fallback 自动加入
  3) `SKILLS_RUNTIME_SDK_CONFIG_PATHS`

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
python3 -m agent_sdk.cli.main skills preflight --workspace-root . --config help/examples/skills.cli.overlay.yaml --pretty
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
