from __future__ import annotations

import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from agent_sdk.core.executor import Executor
from agent_sdk.sandbox import BubblewrapSandboxAdapter, SeatbeltSandboxAdapter
from agent_sdk.tools.builtin.shell_exec import shell_exec
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext


def _mk_ctx(*, workspace_root: Path, sandbox_adapter) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="t_sandbox_effects",
        wal=None,
        executor=Executor(),
        human_io=None,
        env={},
        cancel_checker=None,
        redaction_values=[],
        default_timeout_ms=10_000,
        max_file_bytes=10,
        sandbox_policy_default="restricted",
        sandbox_adapter=sandbox_adapter,
        emit_tool_events=False,
        event_sink=None,
    )


@pytest.mark.skipif(not sys.platform.startswith("darwin"), reason="macOS seatbelt only")  # type: ignore[name-defined]
def test_seatbelt_denies_reading_etc_subpath(tmp_path: Path) -> None:
    """
    证明“真进 OS sandbox”会产生可见限制：
    - seatbelt profile deny /etc 的 file-read*
    - 在 restricted sandbox 下执行 `cat /etc/hosts` 应失败（Operation not permitted）
    - 同时 tool result 里应包含 data.sandbox.active=true
    """

    adapter = SeatbeltSandboxAdapter(profile='(version 1) (deny file-read* (subpath "/etc")) (allow default)')
    if not adapter.is_available():
        pytest.skip("sandbox-exec not available")

    ctx = _mk_ctx(workspace_root=tmp_path, sandbox_adapter=adapter)
    r = shell_exec(ToolCall(call_id="c1", name="shell_exec", args={"argv": ["/bin/cat", "/etc/hosts"]}), ctx)

    assert r.ok is False
    details = r.details or {}
    assert details.get("error_kind") in ("exit_code", None)  # error_kind 可能随 executor 映射调整
    assert "Operation not permitted" in (details.get("stderr") or "")
    assert (details.get("data") or {}).get("sandbox", {}).get("active") is True


def _pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return int(port)


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt: str, *args) -> None:  # silence
        return


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux bubblewrap only")  # type: ignore[name-defined]
def test_bubblewrap_unshare_net_blocks_loopback_to_host(tmp_path: Path) -> None:
    """
    证明 Linux bwrap 的 --unshare-net 会产生可见限制：
    - host 上启动 127.0.0.1 的 http server
    - sandbox 内（unshare-net）访问 host loopback 必失败（没有该服务）
    - tool result 里应包含 data.sandbox.active=true
    """

    adapter = BubblewrapSandboxAdapter(bwrap_path="bwrap", unshare_net=True)
    if not adapter.is_available():
        pytest.skip("bwrap not available")

    port = _pick_free_port()
    server = HTTPServer(("127.0.0.1", port), _OkHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        # 先验证：host（非 sandbox）下请求能成功，避免“服务没起来”的假阳性
        host_ctx = ToolExecutionContext(
            workspace_root=tmp_path,
            run_id="t_sandbox_effects_host",
            wal=None,
            executor=Executor(),
            emit_tool_events=False,
            sandbox_policy_default="none",
            sandbox_adapter=None,
        )
        ok_r = shell_exec(
            ToolCall(
                call_id="c_ok",
                name="shell_exec",
                args={
                    "argv": [
                        "python3",
                        "-c",
                        f"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:{port}', timeout=1).read().decode())",
                    ]
                },
            ),
            host_ctx,
        )
        assert ok_r.ok is True
        assert "ok" in (ok_r.details or {}).get("stdout", "")

        # 再验证：sandbox 内网络隔离后应失败
        ctx = _mk_ctx(workspace_root=tmp_path, sandbox_adapter=adapter)
        r = shell_exec(
            ToolCall(
                call_id="c1",
                name="shell_exec",
                args={
                    "argv": [
                        "python3",
                        "-c",
                        f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}', timeout=1).read()",
                    ]
                },
            ),
            ctx,
        )
        assert r.ok is False
        details = r.details or {}
        assert (details.get("data") or {}).get("sandbox", {}).get("active") is True
    finally:
        server.shutdown()
        server.server_close()
