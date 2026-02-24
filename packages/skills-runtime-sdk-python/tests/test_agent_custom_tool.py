from __future__ import annotations

from pathlib import Path

from agent_sdk import Agent
from agent_sdk.core.errors import UserError
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.state.jsonl_wal import JsonlWal
from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec


def _write_overlay(tmp_path: Path, *, safety_lines: list[str]) -> Path:
    """写入最小 config overlay（仅覆盖 safety 字段）。"""

    overlay = tmp_path / "runtime.yaml"
    overlay.write_text("\n".join(["config_version: 1", *safety_lines, ""]), encoding="utf-8")
    return overlay


class _AlwaysApprove(ApprovalProvider):
    async def request_approval(
        self, *, request: ApprovalRequest, timeout_ms=None  # type: ignore[override]
    ) -> ApprovalDecision:
        _ = request
        _ = timeout_ms
        return ApprovalDecision.APPROVED


def test_agent_custom_tool_default_ask_fails_fast_without_provider(tmp_path: Path) -> None:
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="c1", name="add", args={"x": 1, "y": 2}, raw_arguments='{"x":1,"y":2}')],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            # should not reach (fail-fast)
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="should-not-reach"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path)

    @agent.tool
    def add(x: int, y: int) -> int:
        """add two ints"""

        return x + y

    result = agent.run("use add tool")
    assert result.status == "failed"

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed
    assert failed[-1].payload.get("error_kind") == "config_error"


def test_agent_custom_tool_requires_approvals_when_provider_is_configured(tmp_path: Path) -> None:
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="c1", name="add", args={"x": 1, "y": 2}, raw_arguments='{"x":1,"y":2}')],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=_AlwaysApprove())

    called = {"n": 0}

    @agent.tool
    def add(x: int, y: int) -> int:
        """add two ints"""

        called["n"] += 1
        return x + y

    result = agent.run("use add tool")
    assert result.final_output == "ok"
    assert called["n"] == 1

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    req = [e for e in events if e.type == "approval_requested"]
    dec = [e for e in events if e.type == "approval_decided"]
    req_add = [e for e in req if (e.payload or {}).get("tool") == "add"]
    assert req_add
    approval_key = str(req_add[-1].payload.get("approval_key") or "")
    assert approval_key

    dec_add = [e for e in dec if (e.payload or {}).get("approval_key") == approval_key]
    assert dec_add
    assert dec_add[-1].payload.get("decision") == ApprovalDecision.APPROVED.value
    assert dec_add[-1].payload.get("reason") == "provider"


def test_agent_custom_tool_allowlist_runs_without_approvals(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        safety_lines=[
            "safety:",
            "  mode: ask",
            "  tool_allowlist:",
            "    - add",
        ],
    )

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="c1", name="add", args={"x": 1, "y": 2}, raw_arguments='{"x":1,"y":2}')],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])

    called = {"n": 0}

    @agent.tool
    def add(x: int, y: int) -> int:
        """add two ints"""

        called["n"] += 1
        return x + y

    result = agent.run("use add tool")
    assert result.final_output == "ok"
    assert called["n"] == 1

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert not any(e.type == "approval_requested" for e in events)
    finished = [e for e in events if e.type == "tool_call_finished"]
    assert finished
    assert finished[0].payload["tool"] == "add"
    assert finished[0].payload["result"]["stdout"] == "3"


def test_agent_custom_tool_denylist_blocks_without_approvals(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        safety_lines=[
            "safety:",
            "  mode: ask",
            "  tool_denylist:",
            "    - add",
        ],
    )

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="c1", name="add", args={"x": 1, "y": 2}, raw_arguments='{"x":1,"y":2}')],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])

    called = {"n": 0}

    @agent.tool
    def add(x: int, y: int) -> int:
        """add two ints"""

        called["n"] += 1
        return x + y

    result = agent.run("use add tool")
    assert result.final_output == "ok"
    assert called["n"] == 0

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert not any(e.type == "approval_requested" for e in events)
    finished = [e for e in events if e.type == "tool_call_finished"]
    assert finished
    assert finished[0].payload["tool"] == "add"
    assert finished[0].payload["result"]["error_kind"] == "permission"
    assert finished[0].payload["result"]["data"]["reason"] == "tool_denylist"


def test_agent_register_tool_is_dispatchable_and_obeys_allowlist(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        safety_lines=[
            "safety:",
            "  mode: ask",
            "  tool_allowlist:",
            "    - hello_tool",
        ],
    )

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="c1", name="hello_tool", args={}, raw_arguments="{}")],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])

    spec = ToolSpec(
        name="hello_tool",
        description="say hi",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    def handler(call: ToolCall, _ctx) -> ToolResult:  # type: ignore[no-untyped-def]
        payload = ToolResultPayload(
            ok=True,
            stdout="hi",
            stderr="",
            exit_code=0,
            duration_ms=0,
            truncated=False,
            data={"result": "hi"},
            error_kind=None,
            retryable=False,
            retry_after_ms=None,
        )
        return ToolResult.from_payload(payload)

    agent.register_tool(spec, handler)
    result = agent.run("use hello_tool")
    assert result.final_output == "ok"

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert not any(e.type == "approval_requested" for e in events)
    finished = [e for e in events if e.type == "tool_call_finished"]
    assert finished
    assert finished[0].payload["tool"] == "hello_tool"
    assert finished[0].payload["result"]["stdout"] == "hi"


def test_agent_register_tool_requires_approvals_when_provider_is_configured(tmp_path: Path) -> None:
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="c1", name="hello_tool", args={}, raw_arguments="{}")],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=_AlwaysApprove())

    spec = ToolSpec(
        name="hello_tool",
        description="say hi",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    called = {"n": 0}

    def handler(call: ToolCall, _ctx) -> ToolResult:  # type: ignore[no-untyped-def]
        _ = call
        called["n"] += 1
        payload = ToolResultPayload(
            ok=True,
            stdout="hi",
            stderr="",
            exit_code=0,
            duration_ms=0,
            truncated=False,
            data={"result": "hi"},
            error_kind=None,
            retryable=False,
            retry_after_ms=None,
        )
        return ToolResult.from_payload(payload)

    agent.register_tool(spec, handler)
    result = agent.run("use hello_tool")
    assert result.final_output == "ok"
    assert called["n"] == 1

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    req = [e for e in events if e.type == "approval_requested"]
    dec = [e for e in events if e.type == "approval_decided"]
    req_tool = [e for e in req if (e.payload or {}).get("tool") == "hello_tool"]
    assert req_tool
    approval_key = str(req_tool[-1].payload.get("approval_key") or "")
    assert approval_key

    dec_tool = [e for e in dec if (e.payload or {}).get("approval_key") == approval_key]
    assert dec_tool
    assert dec_tool[-1].payload.get("decision") == ApprovalDecision.APPROVED.value
    assert dec_tool[-1].payload.get("reason") == "provider"


def test_agent_register_tool_default_ask_fails_fast_without_provider(tmp_path: Path) -> None:
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="c1", name="hello_tool", args={}, raw_arguments="{}")],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            # should not reach (fail-fast)
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="should-not-reach"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path)

    spec = ToolSpec(
        name="hello_tool",
        description="say hi",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    def handler(call: ToolCall, _ctx) -> ToolResult:  # type: ignore[no-untyped-def]
        payload = ToolResultPayload(ok=True, stdout="hi", exit_code=0, data={"result": "hi"})
        return ToolResult.from_payload(payload)

    agent.register_tool(spec, handler)
    result = agent.run("use hello_tool")
    assert result.status == "failed"

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed
    assert failed[-1].payload.get("error_kind") == "config_error"


def test_agent_register_tool_denylist_blocks_without_approvals(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        safety_lines=[
            "safety:",
            "  mode: ask",
            "  tool_denylist:",
            "    - hello_tool",
        ],
    )

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="c1", name="hello_tool", args={}, raw_arguments="{}")],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])

    spec = ToolSpec(
        name="hello_tool",
        description="say hi",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    called = {"n": 0}

    def handler(call: ToolCall, _ctx) -> ToolResult:  # type: ignore[no-untyped-def]
        called["n"] += 1
        payload = ToolResultPayload(ok=True, stdout="hi", exit_code=0, data={"result": "hi"})
        return ToolResult.from_payload(payload)

    agent.register_tool(spec, handler)
    result = agent.run("use hello_tool")
    assert result.final_output == "ok"
    assert called["n"] == 0

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert not any(e.type == "approval_requested" for e in events)
    finished = [e for e in events if e.type == "tool_call_finished"]
    assert finished
    assert finished[0].payload["tool"] == "hello_tool"
    assert finished[0].payload["result"]["error_kind"] == "permission"
    assert finished[0].payload["result"]["data"]["reason"] == "tool_denylist"


def test_agent_register_tool_duplicate_rejected_by_default(tmp_path: Path) -> None:
    backend = FakeChatBackend(calls=[FakeChatCall(events=[ChatStreamEvent(type="completed", finish_reason="stop")])])
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path)

    spec = ToolSpec(
        name="t",
        description="t",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )

    def handler(call: ToolCall, _ctx) -> ToolResult:  # type: ignore[no-untyped-def]
        return ToolResult.from_payload(ToolResultPayload(ok=True, stdout="ok", stderr="", exit_code=0, data={}))

    agent.register_tool(spec, handler)
    try:
        agent.register_tool(spec, handler)
        assert False, "expected duplicate registration to raise"
    except UserError:
        pass
