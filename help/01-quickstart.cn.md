<div align="center">

[中文](01-quickstart.cn.md) | [English](01-quickstart.md) | [Help](README.cn.md)

</div>

# 01. Quickstart：20 分钟跑通 SDK + Studio

## 1.1 前置要求

- Python `>=3.10`（必须）
- Node.js（仅 Studio 前端需要）
- 在仓库根目录执行命令：`<repo_root>`

> 说明：若你用系统 Python 3.9，会在导入阶段因 `str | None` 注解报错。

## 1.2 一键离线回归（先验证环境）

```bash
cd <repo_root>
bash scripts/pytest.sh
```

预期：
- root tests 通过
- SDK tests 通过

若失败，先看：`help/09-troubleshooting.md` 的“环境与版本”章节。

## 1.3 SDK 最小运行（Python）

### Step 1：准备 overlay 配置

复制示例：

```bash
cp help/examples/sdk.overlay.yaml /tmp/sdk.overlay.yaml
```

按你环境修改：
- `llm.base_url`
- `llm.api_key_env`
- `models.planner`
- `models.executor`

### Step 2：准备 API key（本地）

```bash
export OPENAI_API_KEY='<your-key>'
```

### Step 3：运行最小脚本

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 help/examples/run_agent_minimal.py --workspace-root . --config /tmp/sdk.overlay.yaml
```

预期输出（示例）：
- 打印 `run_id`
- 持续输出事件类型（`run_started`、`tool_call_*`、`run_completed`）
- 最后输出 `final_output`

## 1.4 Skills CLI 最小验证

### Step 1：准备 skills CLI overlay

```bash
cp help/examples/skills.cli.overlay.yaml /tmp/skills.cli.overlay.yaml
```

### Step 2：preflight

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills preflight \
  --workspace-root . \
  --config /tmp/skills.cli.overlay.yaml \
  --pretty
```

### Step 3：scan

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills scan \
  --workspace-root . \
  --config /tmp/skills.cli.overlay.yaml \
  --pretty
```

预期：stdout 为 JSON，包含 `ok/issues` 或 `report/skills`。

## 1.5 Studio MVP 最小跑通

### 后端

```bash
cd <repo_root>
cp packages/skills-runtime-studio-mvp/backend/.env.example \
   packages/skills-runtime-studio-mvp/backend/.env

bash packages/skills-runtime-studio-mvp/backend/scripts/dev.sh
```

默认地址：`http://127.0.0.1:8000`

健康检查：

```bash
curl -s http://127.0.0.1:8000/api/v1/health | jq .
```

### 前端

另开终端：

```bash
cd <repo_root>
npm -C packages/skills-runtime-studio-mvp/frontend install
npm -C packages/skills-runtime-studio-mvp/frontend run dev
```

默认地址：`http://localhost:5173`

## 1.6 第一次交互建议

1. 创建 Session
2. 检查/设置 skills roots
3. 在 Run 中输入包含合法 mention 的消息：

```text
请调用 $[web:mvp].article-writer 生成一篇 300 字文章，主题是“为什么要做离线回归”。
```

## 1.7 快速自检清单

- [ ] Python 版本 >= 3.10
- [ ] `scripts/pytest.sh` 通过
- [ ] SDK 最小脚本可跑
- [ ] `skills preflight/scan` 返回 JSON
- [ ] Studio 后端 health 正常
- [ ] Studio 前端可创建 session 并发起 run

## 1.8 下一步

- 想改配置：看 `help/02-config-reference.cn.md`
- 想写代码接 SDK：看 `help/03-sdk-python-api.cn.md`
- 想命令行批量操作：看 `help/04-cli-reference.cn.md`

---

上一章：[00. 框架总览](00-overview.cn.md) · 下一章：[02. 配置参考](02-config-reference.cn.md)
