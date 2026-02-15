# Testing Strategy（测试策略：离线门禁 + 可选集成）

目标：在不依赖外网/真实 key 的情况下，也能对 SDK 的核心能力做稳定回归。

---

## 1) 测试分层

### 1.1 离线回归（必须门禁）

特点：
- deterministic
- 不需要真实模型
- 不需要网络

推荐入口：
- `bash scripts/pytest.sh`

常用夹具：
- `agent_sdk.llm.fake.FakeChatBackend`：脚本化 streaming 事件序列

### 1.2 可选集成验证（有环境才跑）

特点：
- 依赖真实模型/网络/系统能力（例如容器 sandbox）
- 不作为 CI 的强门禁（除非明确建立 nightly/手工验证流程）

示例入口：
- `help/examples/run_agent_minimal.py`（需要 `OPENAI_API_KEY`）
- `scripts/integration/os_sandbox_bubblewrap_probe_docker.sh`（Docker 探测）

---

## 2) 示例库的测试策略（为什么要 smoke tests）

示例不是“文档附属物”，而是：
- 教学材料（给编码智能体）
- 回归资产（防止示例漂移/断链）

因此新增 `test_examples_smoke.py`：
- 只跑少量代表性 examples
- 只断言 exit=0 + 关键标记（例如 `EXAMPLE_OK:`）
- 避免长文本断言导致 brittle

