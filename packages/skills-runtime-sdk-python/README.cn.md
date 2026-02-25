<div align="center">

[中文](README.cn.md) | [English](README.md)

</div>

# Skills Runtime SDK（Python）

本目录是 Skills Runtime SDK 的 Python 参考实现。

如果你想最快体验整套能力（Runs + SSE + approvals + sandbox + skills 管理），建议从仓库根目录按 `README.md` 启动 Studio MVP。

### 可选依赖（skills sources：Redis / PgSQL）

本 SDK 对 redis/pgsql sources 使用“可选依赖”策略：不安装 extras 也能 import/跑离线回归，但运行到对应 source 才会给出结构化错误提示。

- 安装 redis source 依赖：
  - editable：`python -m pip install -e ".[dev,redis]"`
  - 非 editable：`python -m pip install "skills-runtime-sdk[redis]"`
- 安装 pgsql source 依赖：
  - editable：`python -m pip install -e ".[dev,pgsql]"`
  - 非 editable：`python -m pip install "skills-runtime-sdk[pgsql]"`
- 安装全部可选依赖：
  - editable：`python -m pip install -e ".[dev,all]"`
  - 非 editable：`python -m pip install "skills-runtime-sdk[all]"`

更多说明请参考 `help/` 手册（尤其是 `help/02-config-reference.md` 与 `help/05-skills-guide.md`）。

## 开发与测试（M1：最小可测骨架）

在本目录下：

- 安装（editable）：
  - `python -m pip install -e ".[dev]"`
- 运行单测：
  - `pytest -q`
- 快速自检 import：
  - `python -c "import skills_runtime; print(skills_runtime.__version__)"`

说明（环境兼容性）：
- 若你的 Python 运行环境因为 locale/编码导致启动时报 `UnicodeDecodeError`（常见于 `.pth` 内含非 ASCII 路径），可临时使用：
  - `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 python ...`

## 你需要配置哪些文件？

### 1) API Key（不落盘到仓库）

1. 复制模板文件：
   - 从 `packages/skills-runtime-sdk-python/.env.example` 复制为 `packages/skills-runtime-sdk-python/.env`
2. 在 `packages/skills-runtime-sdk-python/.env` 中配置：
   - `OPENAI_API_KEY=...`

注意：
- **不要提交** `packages/skills-runtime-sdk-python/.env`（仓库已忽略 `.env*`，仅保留 `.env.example`）。

### 2) 模型 API / Base URL / 模型名

1. 复制模板文件：
   - 从 `packages/skills-runtime-sdk-python/config/runtime.yaml.example` 复制为 `packages/skills-runtime-sdk-python/config/runtime.yaml`
2. 在 `packages/skills-runtime-sdk-python/config/runtime.yaml` 中按你的内网 OpenAI-compatible 服务修改：
   - `llm.base_url`：例如 `http(s)://<your-host>/v1`
   - `llm.api_key_env`：默认 `OPENAI_API_KEY`
   - `models.planner` / `models.executor`：替换为你实际可用的模型名

## 路径速查（后续代码将读取这些位置）

- API Key 配置文件（本地）：`packages/skills-runtime-sdk-python/.env`
- LLM 配置文件：`packages/skills-runtime-sdk-python/config/runtime.yaml`

> 当前阶段仅提供目录与配置模板；等你把内网可用的 OpenAI-compatible API、模型名与 key 配好后，再开始实现功能代码。

## Prompt 配置（系统/开发者提示词可配置）

SDK 默认自带一份最佳实践模板（随 package 分发，不依赖仓库内的过程文档目录）：
- 默认配置：`skills_runtime/assets/default.yaml`
- 默认 prompt 模板：`skills_runtime/assets/prompts/default/{system.md,developer.md}`

你可以用 overlay 配置覆盖（推荐）：
- 在 `config/runtime.yaml` 或其它 overlay 文件中增加：
  - `prompt.template: "default"`（选择内置模板）
  - `prompt.system_text` / `prompt.developer_text`（直接提供文本，优先级最高）
  - `prompt.system_path` / `prompt.developer_path`（从文件加载）

说明：
- overlay 的合并策略是“深度合并 + 后者覆盖前者”，详见 `help/02-config-reference.md`。

## Bootstrap（推荐给 Web/CLI：自动加载 `.env` + 自动发现 overlay + 来源追踪）

SDK 核心（`Agent`）不会隐式读取 `.env` 或自动发现 overlay（避免库代码副作用）。若你希望“开箱即用 + 可排障”，推荐在应用层调用 bootstrap：

- 实现：`skills_runtime.bootstrap.resolve_effective_run_config(...)`
- 说明：见 `help/02-config-reference.md`（overlay 发现顺序与环境变量）

你可以用它得到：
- 有效模型/LLM 配置（session > env > yaml）
- `sources`（每个字段来自哪里，便于 UI 展示/排障）

## Skills CLI（`skills-runtime-sdk skills ...`）

本包提供一个不引入第三方依赖的 CLI（标准库 `argparse`），用于对 Skills 配置做预检与扫描，便于脚本化/CI 排障。

### 安装（editable）

在仓库根目录执行：

```bash
cd packages/skills-runtime-sdk-python
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 python -m pip install -e ".[dev]"
```

安装成功后应可直接运行：

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 skills-runtime-sdk --help
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 skills-runtime-sdk skills --help
```

### 准备一个最小 skills overlay（示例）

> 说明：CLI 的 `--config` 是 overlay YAML（根节点必须是 object/mapping）。相对路径按 `--workspace-root` 解析。

在仓库根目录执行（把 workspace_root 固定为仓库根）：

```bash
cat > /tmp/skills-runtime-sdk-skills-cli-demo.yaml <<'YAML'
skills:
  spaces:
    - id: "fixtures"
      account: "demo"
      domain: "local"
      sources: ["examples-fs"]
  sources:
    - id: "examples-fs"
      type: "filesystem"
      options:
        root: "examples/apps/form_interview_pro/skills"
YAML
```

### 示例：`skills-runtime-sdk skills preflight`

在仓库根目录执行：

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 skills-runtime-sdk skills preflight --workspace-root . --config /tmp/skills-runtime-sdk-skills-cli-demo.yaml --pretty
echo $?
```

约定（exit code）：
- `0`：无 errors 且无 warnings
- `12`：仅 warnings
- `10`：存在 errors
- `2`：参数解析失败（argparse 默认行为；此时 stdout/stderr 不保证为 JSON）

stdout：JSON object（`{issues, stats}`）。除 argparse usage 错误外，尽量在失败场景也输出 JSON。

### 示例：`skills-runtime-sdk skills scan`

在仓库根目录执行：

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 skills-runtime-sdk skills scan --workspace-root . --config /tmp/skills-runtime-sdk-skills-cli-demo.yaml --pretty
echo $?
```

约定（exit code）：
- `0`：`errors=[]` 且 `warnings=[]`
- `12`：`errors=[]` 且 `warnings!=[]`
- `11`：`errors!=[]`
- `2`：参数解析失败（argparse 默认行为）

stdout：JSON object（`ScanReport.to_jsonable()`）。你可以用 Python 做最小解析：

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 skills-runtime-sdk skills scan --workspace-root . --config /tmp/skills-runtime-sdk-skills-cli-demo.yaml \
  | LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 python -c 'import json,sys; print(json.load(sys.stdin)["stats"])'
```
