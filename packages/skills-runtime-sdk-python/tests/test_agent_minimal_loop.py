from __future__ import annotations

import json
from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.tools.protocol import ToolCall


class _AlwaysApprove(ApprovalProvider):
    async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
        return ApprovalDecision.APPROVED


class _AlwaysDeny(ApprovalProvider):
    async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
        return ApprovalDecision.DENIED


def _write_safety_overlay(
    tmp_path: Path,
    *,
    mode: str,
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
) -> Path:
    """
    写入一个最小 safety overlay（用于测试 gate 行为）。

    注意：测试只覆盖本次变更关心的字段，避免把全量配置复制进测试。
    """

    p = tmp_path / "runtime.overlay.yaml"
    al = allowlist or []
    dl = denylist or []
    yaml_text = "safety:\n" f"  mode: \"{mode}\"\n"
    if al:
        yaml_text += "  allowlist:\n" + "".join(f"    - \"{x}\"\n" for x in al)
    else:
        yaml_text += "  allowlist: []\n"
    if dl:
        yaml_text += "  denylist:\n" + "".join(f"    - \"{x}\"\n" for x in dl)
    else:
        yaml_text += "  denylist: []\n"
    p.write_text(yaml_text, encoding="utf-8")
    return p


def test_agent_minimal_loop_executes_tool_and_completes(tmp_path: Path) -> None:
    args = {"path": "hello.txt", "content": "hi", "create_dirs": True}
    call = ToolCall(call_id="c1", name="file_write", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="done"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=_AlwaysApprove())
    result = agent.run("write a file")

    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi"
    assert result.final_output == "done"

    wal_path = Path(result.wal_locator)
    assert wal_path.exists()

    events = list(JsonlWal(wal_path).iter_events())
    assert any(e.type == "run_started" for e in events)
    assert any(e.type == "tool_call_requested" for e in events)
    assert any(e.type == "tool_call_finished" for e in events)
    assert any(e.type == "run_completed" for e in events)


def test_agent_denied_approval_does_not_execute_tool(tmp_path: Path) -> None:
    args = {"path": "blocked.txt", "content": "hi", "create_dirs": True}
    call = ToolCall(call_id="c1", name="file_write", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=_AlwaysDeny())
    result = agent.run("try write")

    assert not (tmp_path / "blocked.txt").exists()
    assert result.wal_locator

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    finished = [e for e in events if e.type == "tool_call_finished"]
    assert finished
    assert finished[0].payload["result"]["error_kind"] == "permission"


def test_agent_no_approval_provider_fails_fast_when_approval_required(tmp_path: Path) -> None:
    """
    当某 tool 需要 approval 但未配置 ApprovalProvider 时：
    - 应避免模型进入无意义的反复重试循环
    - 直接以 config_error fail-fast（并写入 run_failed）
    """

    args = {"argv": ["/bin/echo", "hi"]}
    call = ToolCall(call_id="c1", name="shell_exec", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            # should not reach
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="should-not-reach"), ChatStreamEvent(type="completed")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=None)
    result = agent.run("try shell")

    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed
    assert failed[-1].payload.get("error_kind") == "config_error"


def test_agent_repeated_denied_approval_aborts_to_prevent_loop(tmp_path: Path) -> None:
    """
    同一 approval_key 被重复 denied 时，SDK 应中止 run，避免无限循环。
    """

    args = {"argv": ["/bin/echo", "hi"]}
    call = ToolCall(call_id="c1", name="shell_exec", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=_AlwaysDeny())
    result = agent.run("retry denied")

    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed
    assert failed[-1].payload.get("error_kind") == "approval_denied"


def test_agent_no_approval_provider_fails_fast_for_shell(tmp_path: Path) -> None:
    overlay = _write_safety_overlay(tmp_path, mode="ask", allowlist=[], denylist=[])

    args = {"command": ["/bin/echo", "hi"]}
    call = ToolCall(call_id="c1", name="shell", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            )
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=None, config_paths=[overlay])
    result = agent.run("try shell")
    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert any(e.type == "approval_requested" for e in events)
    failed = [e for e in events if e.type == "run_failed"]
    assert failed[-1].payload.get("error_kind") == "config_error"


def test_agent_no_approval_provider_fails_fast_for_shell_command(tmp_path: Path) -> None:
    overlay = _write_safety_overlay(tmp_path, mode="ask", allowlist=[], denylist=[])

    args = {"command": "echo hi"}
    call = ToolCall(call_id="c1", name="shell_command", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            )
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=None, config_paths=[overlay])
    result = agent.run("try shell_command")
    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert any(e.type == "approval_requested" for e in events)
    failed = [e for e in events if e.type == "run_failed"]
    assert failed[-1].payload.get("error_kind") == "config_error"


def test_agent_no_approval_provider_fails_fast_for_exec_command(tmp_path: Path) -> None:
    overlay = _write_safety_overlay(tmp_path, mode="ask", allowlist=[], denylist=[])

    args = {"cmd": "echo hi"}
    call = ToolCall(call_id="c1", name="exec_command", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            )
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=None, config_paths=[overlay])
    result = agent.run("try exec_command")
    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert any(e.type == "approval_requested" for e in events)
    failed = [e for e in events if e.type == "run_failed"]
    assert failed[-1].payload.get("error_kind") == "config_error"


def test_agent_no_approval_provider_fails_fast_for_write_stdin(tmp_path: Path) -> None:
    overlay = _write_safety_overlay(tmp_path, mode="ask", allowlist=[], denylist=[])

    args = {"session_id": 1, "chars": "hi\n"}
    call = ToolCall(call_id="c1", name="write_stdin", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            )
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=None, config_paths=[overlay])
    result = agent.run("try write_stdin")
    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert any(e.type == "approval_requested" for e in events)
    failed = [e for e in events if e.type == "run_failed"]
    assert failed[-1].payload.get("error_kind") == "config_error"


def test_allowlist_skips_approvals_for_shell_command_and_exec_command(tmp_path: Path) -> None:
    overlay = _write_safety_overlay(tmp_path, mode="ask", allowlist=["echo"], denylist=[])

    call1_args = {"command": "echo hi"}
    call1 = ToolCall(call_id="c1", name="shell_command", args=call1_args, raw_arguments=json.dumps(call1_args, ensure_ascii=False))

    call2_args = {"cmd": "echo hi", "yield_time_ms": 1}
    call2 = ToolCall(call_id="c2", name="exec_command", args=call2_args, raw_arguments=json.dumps(call2_args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call1], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call2], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="done"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    # approval_provider=None: 如果 allowlist 生效，不应触发 approvals，也不应 fail-fast
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=None, config_paths=[overlay])
    result = agent.run("allowlist")
    assert result.status == "completed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert not any(e.type == "approval_requested" for e in events)


def test_mode_deny_blocks_shell_command_without_dispatch(tmp_path: Path) -> None:
    overlay = _write_safety_overlay(tmp_path, mode="deny", allowlist=[], denylist=[])

    args = {"command": "echo hi"}
    call = ToolCall(call_id="c1", name="shell_command", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=None, config_paths=[overlay])
    result = agent.run("deny")
    assert result.status == "completed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    started = [e for e in events if e.type == "tool_call_started"]
    assert not started
    finished = [e for e in events if e.type == "tool_call_finished"]
    assert finished
    assert finished[0].payload["result"]["error_kind"] == "permission"
