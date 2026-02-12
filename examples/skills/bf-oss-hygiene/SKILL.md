---
name: bf-oss-hygiene
description: "开源发布前的排除/脱敏/可复现检查清单：把本地协作与敏感配置留在本地，把可复现的模板与 Help 留在仓库。"
metadata:
  short-description: "开源发布：排除/脱敏/可复现"
---

# bf-oss-hygiene：开源发布前的“排除 + 脱敏 + 可复现”流程

## 0) 目标（你要交付什么）

在不删除本地文件的前提下，让仓库满足：

1) **可开源**：不会把本地协作文件、内部文档、运行时产物、密钥、内网地址推到远端。  
2) **可复现**：开源用户可以用 `.example` 模板 + Help 文档跑通最小示例。  
3) **可维护**：排除规则集中在 `.gitignore`；验证步骤可重复执行。  

## 1) 输入约定（你必须先确认）

在执行前，必须确认（缺失则 ask_human）：

- 开源协议（例如 Apache-2.0 / MIT）
- 哪些目录属于**本地协作**（远端不保存）
- 哪些配置文件属于**敏感 overlay**（只留 `.example`）
- MVP/Example 是否需要默认内置示例 skills（建议：需要）

## 2) 核心原则（必须遵守）

- **不删除文件**：本地协作与敏感文件只做 ignore/排除，不做删除。  
- **不提交密钥**：YAML 只允许声明 `api_key_env`；真实 key 只能在 `.env` 或环境变量里。  
- **不写死内网**：示例必须使用 `http://127.0.0.1:...`、`https://api.openai.com/v1` 或占位符；不能出现真实内部域名。  
- **不泄露机器信息**：文档/示例避免 `/Users/...`、`C:\\Users\\...` 这类绝对路径；用 `<repo_root>` / `$HOME` 占位。  

## 3) 执行步骤（推荐顺序）

### Step A：排除规则（`.gitignore`）

目标：把“本地协作/运行时产物/密钥/依赖缓存”都排除掉。

最小建议（按你的仓库实际情况调整）：

- OS 垃圾文件：`.DS_Store`
- 依赖产物：`node_modules/`、`dist/`
- 运行时产物：`.skills_runtime_sdk/`
- Secrets：`.env`、`**/.env`
- 敏感 overlay：`**/runtime.yaml`、`**/llm.yaml`
- 本地协作文档：`docs/`、`legacy/`、`README_local.md`（或你指定的本地文档目录）

验收：执行 `git status`（或初始化仓库后）不应出现上述文件。

### Step B：敏感配置模板化（`*.example`）

目标：让开源用户能复制模板跑通，同时不泄露内部环境。

做法：

- 保留 `runtime.yaml.example`（可复现模板）
- 本地实际配置为 `runtime.yaml`（ignored）
- `.env.example` 只保留变量名，不写真实 key

验收：

- `runtime.yaml.example` 中不能出现真实 key、不能出现内部域名
- Help/README 里明确“复制 `.example` 为本地文件，不提交远端”

### Step C：文档双语化与导航

目标：让开源用户“看得懂、跑得通、能排障”。

最低要求：

- 根 `README.md`：提供 Quickstart（5~10 分钟能跑） + Help 导览
- `README.md`：英文版（GitHub 默认入口），顶部提供语言切换导航（居中）
- `help/`：中英文版对照，文档之间有 Prev/Next 内链

验收：README/Help 的链接不应指向被排除的目录（例如 `docs/`）。

### Step D：脱敏扫描（必须跑）

建议用“硬规则”先兜底（可按需扩展关键词）：

```bash
# 1) 常见密钥形态（示例：OpenAI key）
rg -n "sk-[A-Za-z0-9]{20,}" -S .

# 2) 机器绝对路径（macOS/Linux 常见）
rg -n "/Users/" -S . --glob '!**/node_modules/**'

# 3) 你的组织/项目敏感关键词（由人类提供）
rg -n "<ORG_KEYWORD>" -S .
```

验收：上述扫描不应命中“会被提交”的文件。

### Step E：最小回归（离线可跑）

目标：确保开源用户跟随 README/Help 不会踩到明显断路。

建议最小集合：

- SDK 单测：`bash scripts/pytest.sh`
- Studio 前端测试（如果有）：`npm -C packages/<studio>/frontend test`
- 沙箱可见限制验证（可选，但强烈建议）：`bash scripts/integration/os_sandbox_restriction_demo.sh`

## 4) 输出契约（你必须输出）

执行本 skill 后，你必须输出一份“可粘贴到 PR/Release”说明，至少包含：

1) 本次新增/调整的 ignore 规则清单  
2) 新增/更新的 `.example` 模板列表  
3) 脱敏扫描使用的命令 + 结果摘要（命中=0）  
4) 回归命令 + 结果摘要  
