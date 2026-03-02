<div align="center">

[中文](12-validation-suites.cn.md) | [English](12-validation-suites.md) | [Help](README.cn.md)

</div>

# 12. 验证用例集索引：测试分层（Tier-0/1/2）与证据产物

> 目标：把“离线确定性门禁（CI gate）”与“真实环境验证（nightly/manual）”分层固化，避免只有连真 LLM 才能测。

## 12.1 分层定义（必须遵守）

| Tier | 目标 | 约束（必须） | 是否进 CI gate |
|---|---|---|---|
| Tier-0（deterministic） | 离线、确定性回归门禁 | 不依赖外网；不依赖真实 API key；不依赖真 LLM 随机输出；结果可重复 | 是 |
| Tier-1（integration） | 环境/系统能力连通性验证 | 允许依赖本机 OS 能力（例如 `sandbox-exec` / `bwrap`）；环境不齐可 skip | 建议 nightly 或手动 |
| Tier-2（real env） | 真环境质量验证 | 允许依赖外网、真实 key、真实 provider | 只能 nightly/manual |

## 12.2 Tier-0：离线确定性门禁（推荐唯一入口）

入口脚本（推荐 CI 只跑它）：

```bash
bash scripts/tier0.sh
```

覆盖内容：

1. Repo + SDK Python 离线单测（含 examples smoke）：

   ```bash
   bash scripts/pytest.sh
   ```

2. Studio 后端离线 E2E（fake LLM + approvals 流回归）：

   ```bash
   bash packages/skills-runtime-studio-mvp/backend/scripts/pytest.sh
   ```

3. Studio 前端单测（UI/状态机/metadata notices）：

   ```bash
   npm -C packages/skills-runtime-studio-mvp/frontend ci
   npm -C packages/skills-runtime-studio-mvp/frontend test
   ```

证据产物（建议在 CI 里保存）：
- pytest 输出（pass/skip/failed）
- 如测试运行会落盘 events/WAL：保存对应目录（可选；按项目实际配置）

### 12.2.1 内部生产：如何“强制要求本地协作文档”但不暴露

约束提醒：
- 本仓库允许把本地协作文件（例如：协作宪法/门禁、文档索引、工作记录与任务总结等）通过 `.gitignore` 排除，以满足“开源不暴露”的要求。
- 但内部生产环境仍可能希望把这些文件作为门禁的一部分进行校验（例如确保 worklog/台账存在）。

做法（显式开关）：
- 默认（开源/公共 CI）：不强制要求这些本地文件存在。
- 内部环境若要强制：设置环境变量 `REQUIRE_LOCAL_DOCS=1`，相关 smoke tests 才会执行强校验。

示例：

```bash
REQUIRE_LOCAL_DOCS=1 bash scripts/tier0.sh
```

说明：
- 该开关只影响“是否强制要求本地协作文档存在”，不会改变 SDK 运行逻辑；
- 内部环境若要在 CI 中启用，建议通过“私有注入/挂载”的方式把这些文件放到 workspace（仍不提交到 Git）。

## 12.3 Tier-1：集成验证（可选；环境不齐可跳过）

OS sandbox 可见限制验证（无需外网；adapter 缺失会显式提示或跳过）：

```bash
# “肉眼可见”的限制效果演示（macOS/Linux）
bash scripts/integration/os_sandbox_restriction_demo.sh

# 三档 profile 宏展开 + 限制效果证据（macOS/Linux；adapter 不可用会输出 skipped）
bash scripts/integration/sandbox_profile_regression.sh dev
bash scripts/integration/sandbox_profile_regression.sh balanced
bash scripts/integration/sandbox_profile_regression.sh prod
```

说明：
- Tier-1 的目标是验证“依赖是否齐全、限制是否生效”，不是门禁；
- 不建议把依赖 `sandbox-exec`/`bwrap` 的用例放进默认 gate，否则会在不同 CI 环境里产生误拦截/误失败。

## 12.4 Tier-2：真实环境验证（nightly/manual）

建议在真实环境跑的集合（示例）：
- 真实 LLM provider（真 key + 真 base_url）下跑 `help/examples/run_agent_minimal.py`
- 真实业务技能库（真实 roots/spaces/sources）下跑 skills preflight/scan
- Linux 生产容器中验证 bubblewrap 可用性（受宿主机 userns / seccomp / apparmor 影响）

约束：
- 必须显式声明依赖（外网、密钥、容器权限、服务地址等）
- 不得作为默认门禁阻断开发迭代

---

返回总览：[`README.cn.md`](./README.cn.md)  
相关：[`10-cookbook.cn.md`](./10-cookbook.cn.md)
