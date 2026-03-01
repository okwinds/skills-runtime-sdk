<div align="center">

[中文](10-cookbook.cn.md) | [English](10-cookbook.md) | [Help](README.cn.md)

</div>

# 10. Cookbook：接入与上线配方

## 配方 1：本地开发（低打扰）

目标：开发效率优先，同时保留基础安全边界。

推荐：
- `safety.mode=ask`
- allowlist 放开高频只读命令
- denylist 拦截破坏命令
- `sandbox.default_policy=restricted`（最小 profile；不要一上来收得太死）

执行清单：
1. 复制 overlay 模板并按需修改：

   ```bash
   cp help/examples/sdk.overlay.yaml /tmp/sdk.overlay.yaml
   ```

2. 注入 API key（不要写入 YAML）：

   ```bash
   export OPENAI_API_KEY='...'
   ```

3. 先跑 skills 预检与扫描（验证 sources + mention + overlay）：

   ```bash
   cp help/examples/skills.cli.overlay.yaml /tmp/skills.cli.overlay.yaml

   PYTHONPATH=packages/skills-runtime-sdk-python/src \
     python3 -m skills_runtime.cli.main skills preflight --workspace-root . --config /tmp/skills.cli.overlay.yaml --pretty

   PYTHONPATH=packages/skills-runtime-sdk-python/src \
     python3 -m skills_runtime.cli.main skills scan --workspace-root . --config /tmp/skills.cli.overlay.yaml --pretty
   ```

4. 跑最小 Python 示例（验证 run + tools/approvals/sandbox 的基础链路）：

   ```bash
   PYTHONPATH=packages/skills-runtime-sdk-python/src \
     python3 help/examples/run_agent_minimal.py --workspace-root . --config /tmp/sdk.overlay.yaml
   ```

## 配方 2：预发验证（稳定性优先）

目标：尽早发现配置漂移与权限问题。

建议：
- 收紧 allowlist
- 保持 denylist 保守
- 固化 overlay 来源，不允许临时环境覆盖
- 每次发布前跑 `scripts/pytest.sh`

## 配方 3：生产部署（Linux）

目标：安全 + 可观测 + 可回滚。

建议：
- `sandbox.default_policy=restricted`
- `os.mode=auto` + `bubblewrap.unshare_net=true`
- approvals 必须可观测（pending + decision 流）
- 保留 rollback 开关：`restricted -> none`

## 配方 4：前端业务接入 SDK（通过 Studio API）

目标：业务前端无需理解内核即可发起 run 与处理审批。

说明：
- Studio MVP 是**下游示例服务**，不定义 SDK/框架契约。
- 本配方仅演示如何对接 Studio 的 REST+SSE API；完整端到端指南见 `help/07-studio-guide.cn.md`。

最小交互：
1. `POST /api/v1/sessions`
2. `PUT /api/v1/sessions/{session_id}/skills/sources`
3. `POST /api/v1/sessions/{session_id}/runs`
4. SSE 订阅 events：`GET /api/v1/runs/{run_id}/events/stream`
5. 如有审批，先 `GET /api/v1/runs/{run_id}/approvals/pending`，再 `POST /api/v1/runs/{run_id}/approvals/{approval_key}` 提交决策

## 配方 5：CI 门禁

建议门禁：
1. Tier-0 单一入口：`bash scripts/tier0.sh`
2. `skills preflight`（使用 CI overlay）
3. `skills scan`（检查 warning/error）
4. 文档检查（README/Help 内链不应断；示例命令可跑通）

## 配方 6：故障演练（每两周）

演练项目：
- sandbox adapter 缺失
- overlay 路径失效
- approval 超时
- sources 错配

要求：
- 记录恢复时间
- 记录实际根因
- 更新 `help/09-troubleshooting.md`

---

上一章：[`09-troubleshooting.cn.md`](./09-troubleshooting.cn.md)  
下一章：[`11-faq.cn.md`](./11-faq.cn.md)
