<div align="center">

[English](03-sdk-python-api.md) | [中文](03-sdk-python-api.cn.md) | [Help](README.md)

</div>

# 03. SDK Python API: Integration and extension

## 3.1 Minimal imports

```python
from pathlib import Path
from agent_sdk import Agent
from agent_sdk.llm.openai_chat import OpenAIChatCompletionsBackend
from agent_sdk.config.loader import AgentSdkLlmConfig
```

## 3.2 Construct a minimal runnable Agent

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

## 3.3 Synchronous run: `run()`

```python
result = agent.run("Summarize the core modules in this repo")
print(result.status)
print(result.final_output)
print(result.events_path)
```

Returns:
- `status`: `completed|failed|cancelled`
- `final_output`: final text output
- `events_path`: path to the event log (`events.jsonl`)

## 3.4 Streaming run: `run_stream()`

```python
for event in agent.run_stream("Give me a test plan"):
    print(event.type, event.ts)
    if event.type == "run_completed":
        print(event.payload.get("final_output"))
```

Common event types:
- `run_started`
- `llm_request_started`
- `tool_call_requested`
- `tool_call_started`
- `tool_call_finished`
- `run_completed` / `run_failed`

## 3.5 Async streaming: `run_stream_async()`

```python
import asyncio

async def main():
    async for event in agent.run_stream_async("Write a step-by-step plan"):
        print(event.type)

asyncio.run(main())
```

## 3.6 Register a custom tool (decorator)

`Agent.tool` can register a Python function as a tool.

```python
from agent_sdk import Agent

@agent.tool(name="sum_numbers", description="Sum two integers")
def sum_numbers(a: int, b: int) -> int:
    return a + b

result = agent.run("Call sum_numbers to compute 7 + 8")
print(result.final_output)
```

Notes:
- Prefer primitive types (`str/int/float/bool`) for tool args
- Return values are serialized into the tool result

## 3.7 Inject an approvals provider (`ApprovalProvider`)

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

## 3.8 Use bootstrap to resolve effective config + sources

```python
from pathlib import Path
from agent_sdk import bootstrap

resolved = bootstrap.resolve_effective_run_config(
    workspace_root=Path(".").resolve(),
    session_settings={},
)

print(resolved.base_url)
print(resolved.api_key_env)
print(resolved.sources)  # source tracking per leaf field
```

## 3.9 Read run metrics (from `events.jsonl`)

```python
from pathlib import Path
from agent_sdk.observability.run_metrics import compute_run_metrics_summary

events_path = Path(".skills_runtime_sdk/runs/<run_id>/events.jsonl")
summary = compute_run_metrics_summary(events_path)
print(summary)
```

## 3.10 Common anti-patterns

- Hardcoding API keys in code
- Passing a wrong `workspace_root` (path boundary / missing config)
- Forgetting to pass `backend`
- Swallowing errors in tools (hard to debug)

## 3.11 Example files

- Minimal run: `help/examples/run_agent_minimal.py`
- Custom tool: `help/examples/run_agent_with_custom_tool.py`

---

Prev: [02. Config Reference](02-config-reference.md) · Next: [04. CLI Reference](04-cli-reference.md)
