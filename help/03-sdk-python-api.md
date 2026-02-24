<div align="center">

[English](03-sdk-python-api.md) | [中文](03-sdk-python-api.cn.md) | [Help](README.md)

</div>

# 03. SDK Python API: Integration and extension

## 3.1 Minimal imports

```python
from pathlib import Path
from agent_sdk import Agent
from agent_sdk import AgentBuilder
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
    # production-grade retry/backoff params (max_retries/base/cap/jitter)
    retry={"max_retries": 3, "base_delay_sec": 0.5, "cap_delay_sec": 8.0, "jitter_ratio": 0.1},
)

backend = OpenAIChatCompletionsBackend(llm_cfg)

agent = Agent(
    workspace_root=workspace_root,
    backend=backend,
    config_paths=[workspace_root / "config" / "runtime.yaml"],
)
```

## 3.2.1 Recommended: AgentBuilder (reduces assembly mistakes)

When you need to inject production components like `wal_backend`, `approval_provider`, or `event_hooks`, use `AgentBuilder`:

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

## 3.3 Synchronous run: `run()`

```python
result = agent.run("Summarize the core modules in this repo")
print(result.status)
print(result.final_output)
print(result.wal_locator)
```

Returns:
- `status`: `completed|failed|cancelled`
- `final_output`: final text output
- `wal_locator`: WAL locator (may be a file path or a `wal://...` URI)

Notes:
- Terminal events (`run_completed/run_failed/run_cancelled`) include `wal_locator`.

## 3.4 Streaming run: `run_stream()`

```python
for event in agent.run_stream("Give me a test plan"):
    print(event.type, event.timestamp)
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

## 3.4.1 Event hooks (observability)

Register one or more hooks to receive every `AgentEvent` (same order as the stream output):

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
- When `safety.mode=ask` (default), custom tools are approval-gated unless explicitly allowlisted via `safety.tool_allowlist`

## 3.6.1 Register a pre-built `ToolSpec + handler` (BL-031)

If you already have a `ToolSpec` and a handler (e.g., building an integration bridge), use `Agent.register_tool(...)`:

```python
from agent_sdk import Agent
from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec

spec = ToolSpec(
    name="hello_tool",
    description="Return a friendly greeting",
    parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
)

def handler(call: ToolCall, _ctx) -> ToolResult:  # type: ignore[no-untyped-def]
    payload = ToolResultPayload(ok=True, stdout="hi", exit_code=0, data={"result": "hi"})
    return ToolResult.from_payload(payload)

agent.register_tool(spec, handler, override=False)
```

Notes:
- Name conflicts follow `ToolRegistry.register`: reject by default, allow override only when `override=True`
- Tools injected via `register_tool` are still **custom tools** for safety governance (Route A)

## 3.7 Inject an approvals provider (`ApprovalProvider`)

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

For unattended runs, use rule-based approvals (fail-closed by default: anything unmatched is denied):

```python
from agent_sdk.safety import ApprovalRule, RuleBasedApprovalProvider

provider = RuleBasedApprovalProvider(rules=[ApprovalRule(tool="shell_exec", decision=ApprovalDecision.DENIED)])
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

wal_locator = str(Path(".skills_runtime_sdk/runs/<run_id>/events.jsonl"))  # file WAL only
summary = compute_run_metrics_summary(wal_locator=wal_locator)
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
