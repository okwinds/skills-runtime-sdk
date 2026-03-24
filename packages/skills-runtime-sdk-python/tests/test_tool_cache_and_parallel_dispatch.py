"""Task 4.4 + 5.4：验证工具注册缓存和两阶段并发派发。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

import pytest

from skills_runtime.core.agent import Agent
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


# ─── Task 4.4：builtin_tool_names 缓存验证 ───────────────────────────────────

def test_builtin_tool_names_cache_populated_after_first_run(tmp_path: Path) -> None:
    """首次 run 后 _builtin_tool_names_cache 应被填充。"""
    from skills_runtime.llm.chat_sse import ChatStreamEvent

    backend = FakeChatBackend(
        calls=[FakeChatCall(events=[
            ChatStreamEvent(type="text_delta", text="ok"),
            ChatStreamEvent(type="completed", finish_reason="stop"),
        ])]
    )
    agent = Agent(backend=backend, workspace_root=tmp_path)

    # 首次 run 前缓存为 None
    assert agent._loop._builtin_tool_names_cache is None

    list(agent.run_stream("hi"))

    # 首次 run 后缓存已填充
    cache = agent._loop._builtin_tool_names_cache
    assert cache is not None
    assert isinstance(cache, frozenset)
    assert len(cache) > 0
    assert "shell_exec" in cache
    assert "file_read" in cache


def test_builtin_tool_names_cache_stable_across_runs(tmp_path: Path) -> None:
    """连续两次 run，缓存内容应保持一致（同一 frozenset 对象或等值）。"""
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="r1"), ChatStreamEvent(type="completed", finish_reason="stop")]),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="r2"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )
    agent = Agent(backend=backend, workspace_root=tmp_path)
    list(agent.run_stream("run1"))
    cache1 = agent._loop._builtin_tool_names_cache

    list(agent.run_stream("run2"))
    cache2 = agent._loop._builtin_tool_names_cache

    assert cache1 is not None and cache2 is not None
    assert cache1 == cache2


def test_register_tool_invalidates_builtin_cache(tmp_path: Path) -> None:
    """register_tool 调用后 _builtin_tool_names_cache MUST 被置 None。"""
    backend = FakeChatBackend(
        calls=[FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")])]
    )
    agent = Agent(backend=backend, workspace_root=tmp_path)
    list(agent.run_stream("hi"))
    assert agent._loop._builtin_tool_names_cache is not None

    @agent.tool
    def my_custom_tool(x: str) -> str:
        """custom"""
        return x

    # 注册新工具后缓存应失效
    assert agent._loop._builtin_tool_names_cache is None


# ─── Task 5.4：两阶段并发派发验证 ────────────────────────────────────────────

class _MultiToolBackend:
    """
    返回两个工具调用的 backend（用于验证并发派发两阶段结构）。
    """

    def __init__(self, tool_a: str, tool_b: str) -> None:
        self._tool_a = tool_a
        self._tool_b = tool_b
        self._call_count = 0

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        self._call_count += 1
        if self._call_count == 1:
            # 第一次：返回两个工具调用
            yield ChatStreamEvent(
                type="tool_calls",
                tool_calls=[
                    ToolCall(call_id="call_a", name=self._tool_a, args={"x": "1"}, raw_arguments='{"x":"1"}'),
                    ToolCall(call_id="call_b", name=self._tool_b, args={"x": "2"}, raw_arguments='{"x":"2"}'),
                ],
            )
            yield ChatStreamEvent(type="completed", finish_reason="tool_calls")
        else:
            # 第二次（工具执行后）：返回最终文本
            yield ChatStreamEvent(type="text_delta", text="done")
            yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_two_tool_calls_both_in_history(tmp_path: Path) -> None:
    """
    两个 tool call 在同一 turn 被批量派发后，history 中 MUST 包含两条 tool 结果，
    且顺序与请求顺序一致（call_a 在 call_b 之前）。
    """
    tool_results: Dict[str, List[str]] = {"order": []}

    def handler_a(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
        tool_results["order"].append("a")
        return ToolResult.from_payload(
            ToolResultPayload(ok=True, stdout="result_a", stderr="", exit_code=0,
                              duration_ms=0, truncated=False, data={}, error_kind=None, retryable=False, retry_after_ms=None),
        )

    def handler_b(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
        tool_results["order"].append("b")
        return ToolResult.from_payload(
            ToolResultPayload(ok=True, stdout="result_b", stderr="", exit_code=0,
                              duration_ms=0, truncated=False, data={}, error_kind=None, retryable=False, retry_after_ms=None),
        )

    # safety.mode=allow：自定义工具无需 approval，避免无 ApprovalProvider 时 run_failed
    import tempfile, yaml as _yaml
    overlay_file = tmp_path / "overlay.yaml"
    overlay_file.write_text(_yaml.safe_dump({"config_version": 1, "safety": {"mode": "allow"}}), encoding="utf-8")

    agent = Agent(
        backend=_MultiToolBackend("tool_a", "tool_b"),
        workspace_root=tmp_path,
        config_paths=[overlay_file],
    )

    spec_a = ToolSpec(name="tool_a", description="a", parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
    spec_b = ToolSpec(name="tool_b", description="b", parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
    agent.register_tool(spec_a, handler_a)
    agent.register_tool(spec_b, handler_b)

    events = list(agent.run_stream("test"))
    event_types = [e.type for e in events]

    assert "run_completed" in event_types, f"期望 run_completed，实际事件序列：{event_types}"

    # 验证两个 tool call 都被执行了
    assert "a" in tool_results["order"]
    assert "b" in tool_results["order"]

    # 验证执行顺序（Phase 2 asyncio.gather，同步 handler 顺序执行，a 先于 b）
    assert tool_results["order"].index("a") < tool_results["order"].index("b")


def test_failed_tool_does_not_interrupt_concurrent_sibling(tmp_path: Path) -> None:
    """
    S1：并发 tool call 中某一个失败（返回 error_kind），其他 MUST 继续执行并写入 history。

    场景：tool_a 抛出 UserError（被 dispatcher 捕获为 error payload），
    tool_b 正常执行。两者的 tool 结果 MUST 都在 history 中。
    """
    from skills_runtime.core.errors import UserError as SdkUserError

    tool_results: Dict[str, List[str]] = {"order": []}

    def handler_fail(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
        """模拟失败 handler：直接抛出 UserError（会被 registry 捕获为 error payload）。"""
        raise SdkUserError("intentional failure for test")

    def handler_ok(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
        """正常执行的 handler。"""
        tool_results["order"].append("ok")
        return ToolResult.from_payload(
            ToolResultPayload(ok=True, stdout="result_ok", stderr="", exit_code=0,
                              duration_ms=0, truncated=False, data={}, error_kind=None, retryable=False, retry_after_ms=None),
        )

    import tempfile, yaml as _yaml
    overlay_file = tmp_path / "overlay.yaml"
    overlay_file.write_text(_yaml.safe_dump({"config_version": 1, "safety": {"mode": "allow"}}), encoding="utf-8")

    agent = Agent(
        backend=_MultiToolBackend("tool_fail", "tool_ok"),
        workspace_root=tmp_path,
        config_paths=[overlay_file],
    )

    spec_fail = ToolSpec(name="tool_fail", description="fail", parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
    spec_ok = ToolSpec(name="tool_ok", description="ok", parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
    agent.register_tool(spec_fail, handler_fail)
    agent.register_tool(spec_ok, handler_ok)

    events = list(agent.run_stream("test"))
    event_types = [e.type for e in events]

    # run 应该完成（两个工具都被处理，即使 tool_fail 失败）
    assert "run_completed" in event_types, f"期望 run_completed，实际：{event_types}"

    # tool_ok MUST 被执行
    assert "ok" in tool_results["order"], "tool_ok 应在 tool_fail 失败后仍然执行"

    # 两个 tool_call_finished 事件都应存在
    finished_events = [e for e in events if e.type == "tool_call_finished"]
    assert len(finished_events) == 2, f"期望 2 个 tool_call_finished，实际 {len(finished_events)} 个"


def test_parallel_dispatch_streams_tool_side_events_from_each_call(tmp_path: Path) -> None:
    """
    每个并发 tool call 产生的旁路事件都必须被独立收集并刷到事件流中。

    回归目标：
    - tool handler 内部 `ctx.emit_event(...)` 写出的事件不能丢失；
    - 同批次多个 call 的旁路事件都必须出现在 `run_stream()` 结果里。
    """
    backend = _MultiToolBackend("tool_a", "tool_b")

    import yaml as _yaml

    overlay_file = tmp_path / "overlay.yaml"
    overlay_file.write_text(_yaml.safe_dump({"config_version": 1, "safety": {"mode": "allow"}}), encoding="utf-8")

    agent = Agent(
        backend=backend,
        workspace_root=tmp_path,
        config_paths=[overlay_file],
    )

    spec_a = ToolSpec(name="tool_a", description="a", parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
    spec_b = ToolSpec(name="tool_b", description="b", parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})

    def _handler(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
        ctx.emit_event(
            AgentEvent(
                type="tool_progress",
                timestamp="2026-03-25T00:00:00Z",
                run_id=ctx.run_id,
                payload={"call_id": call.call_id, "tool": call.name, "marker": call.args["x"]},
            )
        )
        return ToolResult.from_payload(
            ToolResultPayload(
                ok=True,
                stdout=f"result:{call.name}",
                stderr="",
                exit_code=0,
                duration_ms=0,
                truncated=False,
                data={},
                error_kind=None,
                retryable=False,
                retry_after_ms=None,
            )
        )

    agent.register_tool(spec_a, _handler)
    agent.register_tool(spec_b, _handler)

    events = list(agent.run_stream("test"))
    assert any(e.type == "run_completed" for e in events)

    progress_events = [e for e in events if e.type == "tool_progress"]
    assert len(progress_events) == 2
    assert {
        (
            (e.payload or {}).get("call_id"),
            (e.payload or {}).get("tool"),
            (e.payload or {}).get("marker"),
        )
        for e in progress_events
    } == {("call_a", "tool_a", "1"), ("call_b", "tool_b", "2")}

    finished_events = [e for e in events if e.type == "tool_call_finished"]
    assert {
        ((e.payload or {}).get("call_id"), (e.payload or {}).get("tool"))
        for e in finished_events
    } >= {("call_a", "tool_a"), ("call_b", "tool_b")}
