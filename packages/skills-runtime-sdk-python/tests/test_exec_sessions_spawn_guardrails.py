from __future__ import annotations

from pathlib import Path

import pytest

from skills_runtime.core.exec_sessions import ExecSessionManager


def test_spawn_closes_fds_when_popen_fails(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mgr = ExecSessionManager()
    closed_fds: list[int] = []

    def _fake_openpty() -> tuple[int, int]:
        return (111, 222)

    def _fake_close(fd: int) -> None:
        closed_fds.append(fd)

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args, kwargs
        raise RuntimeError("boom")

    monkeypatch.setattr("skills_runtime.core.exec_sessions.pty.openpty", _fake_openpty)
    monkeypatch.setattr("skills_runtime.core.exec_sessions.os.close", _fake_close)
    monkeypatch.setattr("skills_runtime.core.exec_sessions.subprocess.Popen", _fake_popen)

    with pytest.raises(RuntimeError, match="boom"):
        mgr.spawn(argv=["python", "-c", "print('x')"], cwd=tmp_path)

    assert 111 in closed_fds
    assert 222 in closed_fds
