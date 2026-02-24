<div align="center">

[中文](03-sdk-python-api.cn.md) | [English](03-sdk-python-api.md) | [Help](README.cn.md)

</div>

# 03. SDK Python API：代码接入与扩展

## 3.1 最小导入

```python
from pathlib import Path
from agent_sdk import Agent
from agent_sdk import AgentBuilder
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
    # legacy：不配置 llm.retry.max_retries 时作为回退
    max_retries=3,
    # 生产级：参数化退避（base/cap/jitter），并可用 retry.max_retries 覆盖次数
    retry={"base_delay_sec": 0.5, "cap_delay_sec": 8.0, "jitter_ratio": 0.1},
)

backend = OpenAIChatCompletionsBackend(llm_cfg)

agent = Agent(
    workspace_root=workspace_root,
    backend=backend,
    config_paths=[workspace_root / "config" / "runtime.yaml"],
)
```

## 3.2.1 推荐：使用 AgentBuilder（减少组装错误）

当你需要注入 `wal_backend`、`approval_provider`、`event_hooks` 等生产级组件时，推荐使用 `AgentBuilder`：

```python
from pathlib import Path
from agent_sdk import AgentBuilder
from agent_sdk.state.wal_protocol import InMemoryWal

agent = (
    AgentBuilder()
    .workspace_root(Path(".").resolve())
    .backend(backend)
    .wal_backend(InMemoryWal())
    .build()
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
- `events_path`: 事件日志定位符（locator；可能是文件路径，也可能是 `wal://...`）

说明：
- 终态事件（`run_completed/run_failed/run_cancelled`）payload 同时包含 `events_path`（兼容字段）与 `wal_locator`（推荐字段）。

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

## 3.4.1 事件 hooks（可观测性）

你可以注册一个或多个 hooks，接收每一条 `AgentEvent`（顺序与 stream 输出一致）：

```python
from agent_sdk.core.contracts import AgentEvent

seen = []
def hook(ev: AgentEvent) -> None:
    seen.append(ev.type)

agent = Agent(
    workspace_root=Path(".").resolve(),
    backend=backend,
    event_hooks=[hook],
)
```

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
- 当 `safety.mode=ask`（默认）时，自定义工具默认会进入 approvals；只有显式配置 `safety.tool_allowlist` 才会免审批执行

## 3.6.1 注册预构造的 `ToolSpec + handler`（BL-031）

当你已经有预构造的 `ToolSpec` 与 handler（例如做集成桥接/注入自定义工具）时，可使用 `Agent.register_tool(...)`：

```python
from agent_sdk import Agent
from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec

spec = ToolSpec(
    name="hello_tool",
    description="返回一条问候语",
    parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
)

def handler(call: ToolCall, _ctx) -> ToolResult:  # type: ignore[no-untyped-def]
    payload = ToolResultPayload(ok=True, stdout="hi", exit_code=0, data={"result": "hi"})
    return ToolResult.from_payload(payload)

agent.register_tool(spec, handler, override=False)
```

注意：
- 冲突策略与 `ToolRegistry.register` 一致：默认拒绝同名；仅当 `override=True` 时允许显式覆盖
- 通过 `register_tool` 注入的工具同样属于 **自定义工具**，会遵循自定义工具审批门禁（Route A）

## 3.7 注入审批提供者（ApprovalProvider）

```python
from agent_sdk.safety.approvals import ApprovalProvider, ApprovalDecision, ApprovalRequest

class AlwaysApprove(ApprovalProvider):
    async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
        _ = request
        _ = timeout_ms
        return ApprovalDecision.APPROVED

agent = Agent(
    workspace_root=Path(".").resolve(),
    backend=backend,
    config_paths=[Path("config/runtime.yaml")],
    approval_provider=AlwaysApprove(),
)
```

无人值守推荐：规则审批（默认 fail-closed，未命中规则一律拒绝）：

```python
from agent_sdk.safety import ApprovalRule, RuleBasedApprovalProvider

provider = RuleBasedApprovalProvider(rules=[ApprovalRule(tool="shell_exec", decision=ApprovalDecision.DENIED)])
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

events_path = Path(".skills_runtime_sdk/runs/<run_id>/events.jsonl")  # 仅适用于文件型 WAL
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
