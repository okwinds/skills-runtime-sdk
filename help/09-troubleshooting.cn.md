<div align="center">

[中文](09-troubleshooting.cn.md) | [English](09-troubleshooting.md) | [Help](README.cn.md)

</div>

# 09. 故障排查手册（现象 → 定位 → 修复）

## 9.1 快速排障顺序

1. 先看 `events.jsonl`
2. 再看 API 返回的 `error_kind`
3. 检查 workspace / overlay / env
4. 检查 approvals 与 sandbox 依赖

## 9.2 常见故障矩阵

### A) Python 导入即失败（`str | None` 类型错误）

现象：
- Python 3.9 环境运行 CLI/SDK 报 typing 错误

定位：

```bash
python3 -V
```

修复：
- 切换到 Python `>=3.10`

---

### B) `overlay config not found`

现象：
- run 直接失败，提示 overlay 路径不存在

定位：

```bash
echo "$SKILLS_RUNTIME_SDK_CONFIG_PATHS"
```

修复：
- 删除失效路径或改为存在路径
- Studio 推荐用 `backend/config/runtime.yaml`

---

### C) `env file not found`

现象：
- 启动时报 `env file not found`

定位：

```bash
echo "$SKILLS_RUNTIME_SDK_ENV_FILE"
```

修复：

```bash
unset SKILLS_RUNTIME_SDK_ENV_FILE
```

---

### D) `sandbox_denied`

现象：
- tool 执行失败，`error_kind=sandbox_denied`

定位：

```bash
command -v sandbox-exec || true
command -v bwrap || true
```

容器/Docker 额外定位（Linux）：
- 若在 Debian/Ubuntu 容器内启用 `bubblewrap`（`bwrap`），但仍失败，常见是 user namespace 或容器策略限制：

```bash
cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || true
cat /proc/sys/user/max_user_namespaces 2>/dev/null || true
```

- 快速探测通路（需要 privileged；仅用于探测，不建议作为生产默认）：

```bash
bash scripts/integration/os_sandbox_bubblewrap_probe_docker.sh
```

修复：
- 安装缺失适配器
- 或临时将 `sandbox.default_policy` 调整为 `none`（保留 approvals+denylist）

---

### E) run 卡住 / 重复审批

现象：
- 同一类审批反复出现，输出持续滚动

定位：
- 查看 `approval_requested/approval_decided` 事件序列
- 检查是否有有效 ApprovalProvider

修复：
- 确认前端确实提交了 `approved/denied`
- 对可安全命令加入 allowlist
- 检查 `approval_timeout_ms`

---

### F) Skill mention 没生效

现象：
- 文本里写了 mention 但未注入

定位：
- 检查语法是否是合法 mention：`$[namespace].skill_name`（见 `help/13-namespace-mentions.cn.md`）
- 检查 session filesystem_sources 与 skills scan 输出

修复：
- 改为合法 mention
- 修复 sources，重新 scan

---

### G) `target_source must be one of session filesystem_sources`

现象：
- Studio 创建 skill 接口报 400

定位：
- 查看 session 当前 filesystem_sources

修复：
- 先 `PUT /skills/sources` 配置 sources，再创建 skill

## 9.3 排障辅助命令

```bash
# 健康检查
curl -s http://127.0.0.1:8000/api/v1/health | jq .

# pending approvals
curl -s http://127.0.0.1:8000/api/v1/runs/<run_id>/approvals/pending | jq .

# skills preflight/scan
PYTHONPATH=packages/skills-runtime-sdk-python/src python3 -m skills_runtime.cli.main skills preflight --workspace-root . --config /tmp/skills.cli.overlay.yaml --pretty
PYTHONPATH=packages/skills-runtime-sdk-python/src python3 -m skills_runtime.cli.main skills scan --workspace-root . --config /tmp/skills.cli.overlay.yaml --pretty
```

## 9.4 排障留痕要求

建议你在自己的项目里保留一份 worklog（位置自定，不要求入本仓库），每次排障至少记录：
- 时间
- 命令
- 原始错误
- 修复动作
- 结果

---

上一章：[`08-architecture-internals.cn.md`](./08-architecture-internals.cn.md)  
下一章：[`10-cookbook.cn.md`](./10-cookbook.cn.md)
