# Ops & QA（怎么验收 / 怎么回归 / 怎么排障）

本页服务于两类人：
- QA：需要稳定、可复现的回归口径
- Ops/平台：需要可观测证据与排障路径

---

## 1) 回归分层（强烈建议照做）

### 1.1 离线门禁（必须）

```bash
bash scripts/pytest.sh
```

要求：
- deterministic
- 不依赖外网与真实 key
- PR/发布前必跑

### 1.2 可选集成验证（有环境才跑）

- 真模型最小链路：`help/examples/run_agent_minimal.py`
- Docker/bwrap 探测：`scripts/integration/os_sandbox_bubblewrap_probe_docker.sh`
- 真沙箱效果演示：`scripts/integration/os_sandbox_restriction_demo.sh`

---

## 2) 证据字段（不要凭“体感”）

### 2.1 approvals

你应该在 WAL（events.jsonl）里能看到：
- `approval_requested`
- `approval_decided`（`reason` 可能为 `provider/cached/no_provider/timeout`）

### 2.2 sandbox

你应该在 `tool_call_finished.result.data.sandbox` 里能看到：
- `requested`：inherit/none/restricted（来自 tool args）
- `effective`：最终生效策略（inherit 会落到默认策略）
- `adapter`：seatbelt/bubblewrap/None
- `active`：是否真正进入 OS sandbox

---

## 3) 常见排障入口

- “工具执行失败”：`help/09-troubleshooting.cn.md`
- “容器里沙箱能不能用”：`help/sandbox-best-practices.cn.md`（Debian 13 / Ubuntu 20.04/24.04）
- “rg 搜不到协作规格目录”：见 `docs_for_coding_agent/common-pitfalls.md`
