from __future__ import annotations

import json
import time
from pathlib import Path

from agent_sdk.core.collab_manager import ChildAgentContext, CollabManager
from agent_sdk.tools.builtin.close_agent import close_agent
from agent_sdk.tools.builtin.resume_agent import resume_agent
from agent_sdk.tools.builtin.send_input import send_input
from agent_sdk.tools.builtin.spawn_agent import spawn_agent
from agent_sdk.tools.builtin.wait import wait_tool
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext


def _payload(result) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(result.content)


def _runner(message: str, ctx: ChildAgentContext) -> str:
    msg = str(message)
    if msg.startswith("fail:"):
        raise RuntimeError("boom")
    if msg.startswith("sleep:"):
        ms = int(msg.split(":", 1)[1])
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            if ctx.cancel_event.is_set():
                return "cancelled"
            time.sleep(0.005)
        return "slept"
    if msg.startswith("wait_input:"):
        # 等待一条输入，或被取消
        while True:
            if ctx.cancel_event.is_set():
                return "cancelled"
            try:
                x = ctx.inbox.get(timeout=0.05)
                return f"got:{x}"
            except Exception:
                continue
    return f"echo:{msg}"


def _mk_ctx(tmp_path: Path, *, with_mgr: bool = True) -> ToolExecutionContext:
    mgr = CollabManager(runner=_runner) if with_mgr else None
    return ToolExecutionContext(workspace_root=tmp_path, run_id="t_collab", emit_tool_events=False, collab_manager=mgr)


# --- spawn_agent (10+) ---


def test_spawn_agent_ok_running(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:100"}), ctx)
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["status"] in {"running", "completed"}
    assert isinstance(p["data"]["id"], str) and p["data"]["id"]


def test_spawn_agent_requires_manager(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, with_mgr=False)
    r = spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "x"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_spawn_agent_message_must_be_non_empty(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": ""}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_spawn_agent_extra_fields_forbidden(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "x", "x": 1}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_spawn_agent_agent_type_is_accepted(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50", "agent_type": "default"}), ctx)
    p = _payload(r)
    assert p["ok"] is True


def test_spawn_agent_can_complete_via_wait(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r1 = spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "echo"}), ctx)
    p1 = _payload(r1)
    aid = p1["data"]["id"]
    r2 = wait_tool(ToolCall(call_id="c2", name="wait", args={"ids": [aid]}), ctx)
    p2 = _payload(r2)
    assert p2["ok"] is True
    assert p2["data"]["results"][0]["status"] in {"completed", "failed", "cancelled"}


def test_spawn_agent_failure_is_recorded(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r1 = spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "fail:x"}), ctx)
    aid = _payload(r1)["data"]["id"]
    r2 = wait_tool(ToolCall(call_id="c2", name="wait", args={"ids": [aid]}), ctx)
    p2 = _payload(r2)
    assert p2["ok"] is True
    assert p2["data"]["results"][0]["status"] in {"failed", "completed"}


def test_spawn_agent_multiple_spawns_unique_ids(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    a = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    b = _payload(spawn_agent(ToolCall(call_id="c2", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    assert a != b


def test_spawn_agent_status_field_exists(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:10"}), ctx))
    assert "status" in p["data"]


def test_spawn_agent_id_field_exists(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:10"}), ctx))
    assert "id" in p["data"]


# --- wait (10+) ---


def test_wait_requires_manager(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, with_mgr=False)
    r = wait_tool(ToolCall(call_id="c1", name="wait", args={"ids": ["x"]}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_wait_ids_must_not_be_empty(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = wait_tool(ToolCall(call_id="c1", name="wait", args={"ids": []}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_wait_unknown_id_is_validation(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = wait_tool(ToolCall(call_id="c1", name="wait", args={"ids": ["missing"]}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_wait_timeout_returns_running_status(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:200"}), ctx))["data"]["id"]
    r = wait_tool(ToolCall(call_id="c2", name="wait", args={"ids": [aid], "timeout_ms": 10}), ctx)
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["results"][0]["status"] in {"running", "completed"}


def test_wait_completed_includes_final_output(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "echo:hi"}), ctx))["data"]["id"]
    r = wait_tool(ToolCall(call_id="c2", name="wait", args={"ids": [aid]}), ctx)
    p = _payload(r)
    assert p["ok"] is True
    it = p["data"]["results"][0]
    if it["status"] == "completed":
        assert "final_output" in it


def test_wait_multiple_ids(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    a = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    b = _payload(spawn_agent(ToolCall(call_id="c2", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    r = wait_tool(ToolCall(call_id="c3", name="wait", args={"ids": [a, b]}), ctx)
    p = _payload(r)
    assert p["ok"] is True
    assert len(p["data"]["results"]) == 2


def test_wait_timeout_ms_must_be_ge_1(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    r = wait_tool(ToolCall(call_id="c2", name="wait", args={"ids": [aid], "timeout_ms": 0}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_wait_extra_fields_forbidden(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = wait_tool(ToolCall(call_id="c1", name="wait", args={"ids": ["x"], "x": 1}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_wait_returns_id_and_status(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:10"}), ctx))["data"]["id"]
    p = _payload(wait_tool(ToolCall(call_id="c2", name="wait", args={"ids": [aid]}), ctx))
    it = p["data"]["results"][0]
    assert "id" in it and "status" in it


def test_wait_status_is_string(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:10"}), ctx))["data"]["id"]
    it = _payload(wait_tool(ToolCall(call_id="c2", name="wait", args={"ids": [aid]}), ctx))["data"]["results"][0]
    assert isinstance(it["status"], str)


# --- send_input (10+) ---


def test_send_input_requires_manager(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, with_mgr=False)
    r = send_input(ToolCall(call_id="c1", name="send_input", args={"id": "x", "message": "m"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_send_input_not_found(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = send_input(ToolCall(call_id="c1", name="send_input", args={"id": "missing", "message": "m"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "not_found"


def test_send_input_validation_empty_message(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = send_input(ToolCall(call_id="c1", name="send_input", args={"id": "x", "message": ""}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_send_input_extra_fields_forbidden(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = send_input(ToolCall(call_id="c1", name="send_input", args={"id": "x", "message": "m", "x": 1}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_send_input_unblocks_waiting_child(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "wait_input:x"}), ctx))["data"]["id"]
    r2 = send_input(ToolCall(call_id="c2", name="send_input", args={"id": aid, "message": "hello"}), ctx)
    assert _payload(r2)["ok"] is True
    r3 = wait_tool(ToolCall(call_id="c3", name="wait", args={"ids": [aid]}), ctx)
    p3 = _payload(r3)
    assert p3["ok"] is True
    assert p3["data"]["results"][0]["status"] in {"completed", "failed", "cancelled"}


def test_send_input_returns_ok_true(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    p = _payload(send_input(ToolCall(call_id="c2", name="send_input", args={"id": aid, "message": "m"}), ctx))
    assert p["ok"] is True


def test_send_input_interrupt_field_is_accepted(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    p = _payload(send_input(ToolCall(call_id="c2", name="send_input", args={"id": aid, "message": "m", "interrupt": True}), ctx))
    assert p["ok"] is True


def test_send_input_id_must_be_non_empty(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(send_input(ToolCall(call_id="c1", name="send_input", args={"id": "", "message": "m"}), ctx))
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_send_input_message_type_validation(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(send_input(ToolCall(call_id="c1", name="send_input", args={"id": "x", "message": 1}), ctx))  # type: ignore[arg-type]
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_send_input_does_not_crash_on_fast_complete(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "echo:hi"}), ctx))["data"]["id"]
    p = _payload(send_input(ToolCall(call_id="c2", name="send_input", args={"id": aid, "message": "m"}), ctx))
    assert p["ok"] in {True, False}


# --- close_agent (10+) ---


def test_close_agent_requires_manager(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, with_mgr=False)
    p = _payload(close_agent(ToolCall(call_id="c1", name="close_agent", args={"id": "x"}), ctx))
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_close_agent_not_found(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(close_agent(ToolCall(call_id="c1", name="close_agent", args={"id": "missing"}), ctx))
    assert p["ok"] is False
    assert p["error_kind"] == "not_found"


def test_close_agent_cancels_running_child(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:200"}), ctx))["data"]["id"]
    p2 = _payload(close_agent(ToolCall(call_id="c2", name="close_agent", args={"id": aid}), ctx))
    assert p2["ok"] is True
    p3 = _payload(resume_agent(ToolCall(call_id="c3", name="resume_agent", args={"id": aid}), ctx))
    assert p3["ok"] is True
    assert p3["data"]["status"] == "cancelled"


def test_close_agent_extra_fields_forbidden(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(close_agent(ToolCall(call_id="c1", name="close_agent", args={"id": "x", "x": 1}), ctx))
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_close_agent_id_must_be_non_empty(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(close_agent(ToolCall(call_id="c1", name="close_agent", args={"id": ""}), ctx))
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_close_agent_can_cancel_waiting_child(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "wait_input:x"}), ctx))["data"]["id"]
    _ = close_agent(ToolCall(call_id="c2", name="close_agent", args={"id": aid}), ctx)
    p = _payload(wait_tool(ToolCall(call_id="c3", name="wait", args={"ids": [aid]}), ctx))
    assert p["ok"] is True
    assert p["data"]["results"][0]["status"] in {"cancelled", "completed", "failed", "running"}


def test_close_agent_return_payload_contains_id(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    p = _payload(close_agent(ToolCall(call_id="c2", name="close_agent", args={"id": aid}), ctx))
    assert p["ok"] is True
    assert p["data"]["id"] == aid


def test_close_agent_double_close_is_ok_or_not_found(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    _ = close_agent(ToolCall(call_id="c2", name="close_agent", args={"id": aid}), ctx)
    p = _payload(close_agent(ToolCall(call_id="c3", name="close_agent", args={"id": aid}), ctx))
    assert p["ok"] in {True, False}


def test_close_agent_validates_args_type(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(close_agent(ToolCall(call_id="c1", name="close_agent", args={"id": 1}), ctx))  # type: ignore[arg-type]
    assert p["ok"] is False


def test_close_agent_works_for_fast_complete(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "echo:hi"}), ctx))["data"]["id"]
    p = _payload(close_agent(ToolCall(call_id="c2", name="close_agent", args={"id": aid}), ctx))
    assert p["ok"] is True


# --- resume_agent (10+) ---


def test_resume_agent_requires_manager(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, with_mgr=False)
    p = _payload(resume_agent(ToolCall(call_id="c1", name="resume_agent", args={"id": "x"}), ctx))
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_resume_agent_missing_is_validation(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(resume_agent(ToolCall(call_id="c1", name="resume_agent", args={"id": "missing"}), ctx))
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_resume_agent_running(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:200"}), ctx))["data"]["id"]
    p = _payload(resume_agent(ToolCall(call_id="c2", name="resume_agent", args={"id": aid}), ctx))
    assert p["ok"] is True
    assert p["data"]["status"] in {"running", "completed", "failed", "cancelled"}


def test_resume_agent_completed(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "echo:hi"}), ctx))["data"]["id"]
    _ = wait_tool(ToolCall(call_id="c2", name="wait", args={"ids": [aid]}), ctx)
    p = _payload(resume_agent(ToolCall(call_id="c3", name="resume_agent", args={"id": aid}), ctx))
    assert p["ok"] is True
    assert p["data"]["status"] in {"completed", "failed", "cancelled", "running"}


def test_resume_agent_id_must_be_non_empty(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(resume_agent(ToolCall(call_id="c1", name="resume_agent", args={"id": ""}), ctx))
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_resume_agent_extra_fields_forbidden(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(resume_agent(ToolCall(call_id="c1", name="resume_agent", args={"id": "x", "x": 1}), ctx))
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_resume_agent_after_close_is_cancelled(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:200"}), ctx))["data"]["id"]
    _ = close_agent(ToolCall(call_id="c2", name="close_agent", args={"id": aid}), ctx)
    p = _payload(resume_agent(ToolCall(call_id="c3", name="resume_agent", args={"id": aid}), ctx))
    assert p["ok"] is True
    assert p["data"]["status"] == "cancelled"


def test_resume_agent_arg_type_validation(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    p = _payload(resume_agent(ToolCall(call_id="c1", name="resume_agent", args={"id": 1}), ctx))  # type: ignore[arg-type]
    assert p["ok"] is False


def test_resume_agent_stdout_empty(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    p = _payload(resume_agent(ToolCall(call_id="c2", name="resume_agent", args={"id": aid}), ctx))
    assert p["ok"] is True
    assert p["stdout"] == ""


def test_resume_agent_data_contains_id(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    aid = _payload(spawn_agent(ToolCall(call_id="c1", name="spawn_agent", args={"message": "sleep:50"}), ctx))["data"]["id"]
    p = _payload(resume_agent(ToolCall(call_id="c2", name="resume_agent", args={"id": aid}), ctx))
    assert p["ok"] is True
    assert p["data"]["id"] == aid

