<div align="center">

[中文](03-sdk-python-api.cn.md) | [English](03-sdk-python-api.md) | [Help](README.cn.md)

</div>

# 03. SDK Python API：代码接入与扩展

## 3.1 最小导入

```python
from pathlib import Path
from agent_sdk import Agent
from agent_sdk.llm.openai_chat import OpenAIChatCompletionsBackend
from agent_sdk.config.loader import AgentSdkLlmConfig
```

## 3.2 构造 Agent（最小可运行）

```python
from pathlib import Path
from agent_sdk import Agent
from agent_sdk.llm.openai_chat import OpenAIChatCompletionsBackend
from agent_sdk.config.loader import AgentSdkLlmConfig

workspace_root = Path(".").resolve()

llm_cfg = AgentSdkLlmConfig(
    base_url="https://api.openai.com/v1",
    api_key_env="OPENAI_API_KEY",
    timeout_sec=60,
    max_retries=3,
)

backend = OpenAIChatCompletionsBackend(llm_cfg)

agent = Agent(
    workspace_root=workspace_root,
    backend=backend,
    config_paths=[workspace_root / "config" / "runtime.yaml"],
)
```

## 3.3 同步运行：`run()`

```python
result = agent.run("请总结当前仓库的核心模块")
print(result.status)
print(result.final_output)
print(result.events_path)
```

返回：
- `status`: `completed|failed|cancelled`
- `final_output`: 最终输出文本
- `events_path`: 事件日志路径（`events.jsonl`）

## 3.4 流式运行：`run_stream()`

```python
for event in agent.run_stream("请给出测试计划"):
    print(event.type, event.ts)
    if event.type == "run_completed":
        print(event.payload.get("final_output"))
```

典型事件：
- `run_started`
- `llm_request_started`
- `tool_call_requested`
- `tool_call_started`
- `tool_call_finished`
- `run_completed` / `run_failed`

## 3.5 异步流式：`run_stream_async()`

```python
import asyncio

async def main():
    async for event in agent.run_stream_async("请输出一步一步方案"):
        print(event.type)

asyncio.run(main())
```

## 3.6 注册自定义工具（decorator）

`Agent.tool` 支持把 Python 函数直接注册为 tool。

```python
from agent_sdk import Agent

@agent.tool(name="sum_numbers", description="计算两个整数之和")
def sum_numbers(a: int, b: int) -> int:
    return a + b

result = agent.run("请调用 sum_numbers 计算 7 + 8")
print(result.final_output)
```

注意：
- 参数类型建议使用基础类型（`str/int/float/bool`）
- 返回值会被转成字符串写入 tool result

## 3.7 注入审批提供者（ApprovalProvider）

```python
from agent_sdk.safety.approvals import ApprovalProvider, ApprovalDecision, ApprovalRequest

class AlwaysApprove(ApprovalProvider):
    async def decide(self, req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(decision="approved")

agent = Agent(
    workspace_root=Path(".").resolve(),
    backend=backend,
    config_paths=[Path("config/runtime.yaml")],
    approval_provider=AlwaysApprove(),
)
```

## 3.8 使用 bootstrap 解析有效配置与来源

```python
from pathlib import Path
from agent_sdk import bootstrap

resolved = bootstrap.resolve_effective_run_config(
    workspace_root=Path(".").resolve(),
    session_settings={},
)

print(resolved.base_url)
print(resolved.api_key_env)
print(resolved.sources)  # 字段来源追踪
```

## 3.9 读取运行指标（基于 events.jsonl）

```python
from pathlib import Path
from agent_sdk.observability.run_metrics import compute_run_metrics_summary

events_path = Path(".skills_runtime_sdk/runs/<run_id>/events.jsonl")
summary = compute_run_metrics_summary(events_path)
print(summary)
```

## 3.10 常见代码反例

- 在代码里写死 API key
- `workspace_root` 传错导致路径越界/找不到配置
- 忘记传 `backend`（运行时会失败）
- 在 tool 函数里吞错不抛异常，导致排障困难

## 3.11 实战示例文件

- 最小运行：`help/examples/run_agent_minimal.py`
- 自定义 tool：`help/examples/run_agent_with_custom_tool.py`

---

上一章：[02. 配置参考](02-config-reference.cn.md) · 下一章：[04. CLI 参考](04-cli-reference.cn.md)
