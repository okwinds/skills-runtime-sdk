from __future__ import annotations

from pathlib import Path

import pytest

import skills_runtime.runtime.client as runtime_client_module
from skills_runtime.runtime.client import RuntimeClient, RuntimeServerInfo


def test_runtime_client_extends_timeout_for_collab_wait(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client = RuntimeClient(workspace_root=tmp_path)
    info = RuntimeServerInfo(pid=123, secret="s", socket_path=str(tmp_path / "runtime.sock"), created_at_ms=1)
    observed: dict[str, float] = {}

    monkeypatch.setattr(client, "ensure_server", lambda: info)

    def _fake_call_with_info(_info, *, method, params=None, timeout_sec=0.0):  # type: ignore[no-untyped-def]
        observed["method"] = method
        observed["timeout_sec"] = float(timeout_sec)
        return {"ok": True}

    monkeypatch.setattr(client, "_call_with_info", _fake_call_with_info)

    client.call(method="collab.wait", params={"ids": ["c1"], "timeout_ms": 5_500})

    assert observed["method"] == "collab.wait"
    assert observed["timeout_sec"] > 5.5


def test_runtime_client_extends_timeout_for_exec_write(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client = RuntimeClient(workspace_root=tmp_path)
    info = RuntimeServerInfo(pid=123, secret="s", socket_path=str(tmp_path / "runtime.sock"), created_at_ms=1)
    observed: dict[str, float] = {}

    monkeypatch.setattr(client, "ensure_server", lambda: info)

    def _fake_call_with_info(_info, *, method, params=None, timeout_sec=0.0):  # type: ignore[no-untyped-def]
        observed["method"] = method
        observed["timeout_sec"] = float(timeout_sec)
        return {"ok": True}

    monkeypatch.setattr(client, "_call_with_info", _fake_call_with_info)

    client.call(method="exec.write", params={"session_id": 1, "yield_time_ms": 6_500})

    assert observed["method"] == "exec.write"
    assert observed["timeout_sec"] > 6.5


def test_ensure_server_does_not_cleanup_live_unresponsive_server(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client = RuntimeClient(workspace_root=tmp_path)
    client._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    client._paths.socket_path.write_text("", encoding="utf-8")
    client._paths.server_info_path.write_text("{}", encoding="utf-8")

    info = RuntimeServerInfo(
        pid=4242,
        secret="secret",
        socket_path=str(client._paths.socket_path),
        created_at_ms=1,
    )

    monkeypatch.setattr(
        client,
        "_read_server_info_state",
        lambda: runtime_client_module._ServerInfoReadResult(state="valid", info=info),
    )
    monkeypatch.setattr(runtime_client_module, "_pid_alive", lambda _pid: True)

    cleanup_calls: list[str] = []

    def _cleanup() -> None:
        cleanup_calls.append("cleanup")

    monkeypatch.setattr(client, "_cleanup_stale_server_files", _cleanup)

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("ping failed")

    monkeypatch.setattr(client, "_call_with_info", _boom)

    with pytest.raises(RuntimeError, match="unresponsive|ping failed"):
        client.ensure_server()

    assert cleanup_calls == []


def test_ensure_server_recovers_when_ping_transport_is_broken(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client = RuntimeClient(workspace_root=tmp_path, start_timeout_ms=100)
    client._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    client._paths.socket_path.write_text("", encoding="utf-8")

    stale_info = RuntimeServerInfo(
        pid=4242,
        secret="secret",
        socket_path=str(client._paths.socket_path),
        created_at_ms=1,
    )
    fresh_info = RuntimeServerInfo(
        pid=5252,
        secret="fresh-secret",
        socket_path=str(client._paths.socket_path),
        created_at_ms=2,
    )

    read_calls = {"count": 0}

    def _read_server_info_state():  # type: ignore[no-untyped-def]
        read_calls["count"] += 1
        if read_calls["count"] == 1:
            return runtime_client_module._ServerInfoReadResult(state="valid", info=stale_info)
        return runtime_client_module._ServerInfoReadResult(state="valid", info=fresh_info)

    monkeypatch.setattr(client, "_read_server_info_state", _read_server_info_state)
    monkeypatch.setattr(runtime_client_module, "_pid_alive", lambda _pid: True)

    cleanup_calls: list[str] = []

    def _cleanup() -> None:
        cleanup_calls.append("cleanup")

    monkeypatch.setattr(client, "_cleanup_stale_server_files", _cleanup)

    popen_calls: list[tuple[object, object]] = []

    class _DummyPopen:
        pass

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        popen_calls.append((args, kwargs))
        return _DummyPopen()

    monkeypatch.setattr(runtime_client_module.subprocess, "Popen", _fake_popen)

    def _fake_call_with_info(_info, *, method, params=None, timeout_sec=0.0):  # type: ignore[no-untyped-def]
        raise ConnectionResetError("transport reset")

    monkeypatch.setattr(client, "_call_with_info", _fake_call_with_info)

    info = client.ensure_server()

    assert info == fresh_info
    assert cleanup_calls == ["cleanup"]
    assert len(popen_calls) == 1


def test_ensure_server_does_not_cleanup_invalid_server_info_when_socket_still_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    client = RuntimeClient(workspace_root=tmp_path, start_timeout_ms=20)
    client._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    client._paths.socket_path.write_text("", encoding="utf-8")
    client._paths.server_info_path.write_text("{invalid", encoding="utf-8")

    cleanup_calls: list[str] = []

    def _cleanup() -> None:
        cleanup_calls.append("cleanup")

    monkeypatch.setattr(client, "_cleanup_stale_server_files", _cleanup)

    popen_calls: list[tuple[object, object]] = []

    class _DummyPopen:
        pass

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        popen_calls.append((args, kwargs))
        return _DummyPopen()

    monkeypatch.setattr(runtime_client_module.subprocess, "Popen", _fake_popen)

    with pytest.raises(RuntimeError, match="invalid|discovery|metadata"):
        client.ensure_server()

    assert cleanup_calls == []
    assert popen_calls == []
