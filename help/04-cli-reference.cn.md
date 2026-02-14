<div align="center">

[中文](04-cli-reference.cn.md) | [English](04-cli-reference.md) | [Help](README.cn.md)

</div>

# 04. CLI 参考：`skills-runtime-sdk` 命令全集

## 4.1 基本形式

```bash
skills-runtime-sdk <command> <subcommand> [flags]
```

若不安装 entrypoint，也可用：

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main <command> <subcommand> [flags]
```

## 4.2 公共参数（多数子命令）

- `--workspace-root`：工作区根目录，默认 `.`
- `--config`：overlay YAML，可重复传入
- `--pretty`：美化 JSON 输出
- `--no-dotenv`：禁用 `.env` 自动加载

## 4.3 命令分组

1. `skills`：配置预检与扫描
2. `tools`：内置工具调用（Codex parity）
3. `runs`：运行指标汇总

---

## 4.4 `skills` 子命令

### `skills preflight`

用途：校验 skills 配置与可用性，不执行真正任务。

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills preflight \
  --workspace-root . \
  --config help/examples/skills.cli.overlay.yaml \
  --pretty
```

### `skills scan`

用途：metadata-only 扫描 skills，不读取大 body。

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills scan \
  --workspace-root . \
  --config help/examples/skills.cli.overlay.yaml \
  --pretty
```

---

## 4.5 `tools` 子命令（全量）

### 文件与搜索

- `tools list-dir`
- `tools grep-files`
- `tools read-file`
- `tools apply-patch`（写操作需 `--yes`）

### Shell / Exec

- `tools shell`
- `tools shell-command`
- `tools exec-command`
- `tools write-stdin`

### 工作流与交互

- `tools update-plan`
- `tools request-user-input`
- `tools view-image`
- `tools web-search`（默认关闭，需 provider）

### 多 agent（协作）

- `tools spawn-agent`
- `tools wait`
- `tools send-input`
- `tools close-agent`
- `tools resume-agent`

---

## 4.6 常用命令示例

### 示例 1：读取文件片段

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main tools read-file \
  --workspace-root . \
  --file-path help/README.md \
  --offset 1 \
  --limit 80 \
  --pretty
```

### 示例 2：执行 shell（argv）

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main tools shell \
  --workspace-root . \
  --yes \
  --timeout-ms 30000 \
  --sandbox inherit \
  -- echo hello
```

### 示例 3：PTY 会话

```bash
# 启动
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main tools exec-command \
  --workspace-root . \
  --yes \
  --cmd "python -u -c \"print('ready'); import time; time.sleep(2)\"" \
  --pretty

# 再写入
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main tools write-stdin \
  --workspace-root . \
  --yes \
  --session-id <id> \
  # PTY 往往处于 canonical mode；CR 更接近“按下回车”。
  --chars $'hello\r' \
  --pretty
```

备注：
- `exec-command` / `write-stdin` 由 workspace 本地 runtime 服务托管，因此 `session-id` 可跨多次 CLI 调用复用。
- runtime 产物位于 `<workspace_root>/.skills_runtime_sdk/runtime/`（server 信息 + 启动日志）。当路径过长时，socket 会降级到 `/tmp/...sock`（仍为 0600 权限）。

---

## 4.7 `runs` 子命令

### `runs metrics`

基于 `events.jsonl` 计算 run 指标。

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main runs metrics \
  --workspace-root . \
  --run-id <run_id> \
  --pretty
```

或直接传 `--events-path`。

---

## 4.8 返回码约定（重点）

### `skills`

- `0`：成功
- `10/11/12`：配置或扫描错误/告警（见 `skills-cli.md`）

### `tools`（按 `error_kind` 映射）

- `0`：ok
- `20`：validation
- `21`：permission
- `22`：not_found
- `23`：unknown
- `24`：sandbox_denied
- `25`：timeout
- `26`：human_required
- `27`：cancelled

## 4.9 CLI 使用建议

- 先 `skills preflight` 再 `skills scan`
- 涉及写操作的命令必须显式加 `--yes`
- 在 CI 中始终启用 `--pretty` + 保存 stdout JSON

---

上一章：[03. SDK Python API](03-sdk-python-api.cn.md) · 下一章：[05. Skills 指南](05-skills-guide.cn.md)
