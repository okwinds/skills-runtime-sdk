from __future__ import annotations

from pathlib import Path

import pytest

from agent_sdk.core.errors import UserError
from agent_sdk.state.jsonl_wal import JsonlWal
from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec, tool_spec_to_openai_tool
from agent_sdk.tools.registry import ToolExecutionContext, ToolRegistry


def test_tool_registry_duplicate_register_rejected(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="r1")
    reg = ToolRegistry(ctx=ctx)

    spec = ToolSpec(
        name="t1",
        description="test tool",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    def handler(_call: ToolCall, _ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult.from_payload(ToolResultPayload(ok=True, stdout="ok", exit_code=0, duration_ms=1))

    reg.register(spec, handler)
    with pytest.raises(UserError):
        reg.register(spec, handler)


def test_tool_registry_override_allowed(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="r1")
    reg = ToolRegistry(ctx=ctx)

    spec = ToolSpec(
        name="t1",
        description="test tool",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    def handler_a(_call: ToolCall, _ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult.from_payload(ToolResultPayload(ok=True, stdout="a", exit_code=0, duration_ms=1))

    def handler_b(_call: ToolCall, _ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult.from_payload(ToolResultPayload(ok=True, stdout="b", exit_code=0, duration_ms=1))

    reg.register(spec, handler_a)
    reg.register(spec, handler_b, override=True)


def test_tool_spec_to_openai_tool_shape() -> None:
    spec = ToolSpec(
        name="file_read",
        description="read file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    )
    tool = tool_spec_to_openai_tool(spec)

    assert tool["type"] == "function"
    assert tool["function"]["name"] == "file_read"
    assert tool["function"]["description"] == "read file"
    assert tool["function"]["parameters"]["type"] == "object"


def test_tool_dispatch_writes_wal_events(tmp_path: Path) -> None:
    wal = JsonlWal(tmp_path / "events.jsonl")
    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="r1", wal=wal)
    reg = ToolRegistry(ctx=ctx)

    spec = ToolSpec(
        name="noop",
        description="noop",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    def handler(call: ToolCall, _ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult.from_payload(ToolResultPayload(ok=True, stdout="ok", exit_code=0, duration_ms=1))

    reg.register(spec, handler)
    reg.dispatch(ToolCall(call_id="c1", name="noop", args={}))

    events = list(wal.iter_events())
    assert len(events) == 3
    assert events[0].type == "tool_call_requested"
    assert events[1].type == "tool_call_started"
    assert events[2].type == "tool_call_finished"
