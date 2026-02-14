from __future__ import annotations

from pathlib import Path

import pytest

from agent_sdk.core.exec_sessions import PersistentExecSessionManager
from agent_sdk.runtime.client import RuntimeClient


def test_persistent_exec_sessions_close_terminates_session(tmp_path: Path) -> None:
    """
    回归（BL-004 完整性）：runtime-backed exec sessions 必须支持显式 close。

    背景：
    - exec sessions 的“跨进程复用”由 runtime server 托管；
    - 若缺少 close，会导致长驻 PTY 子进程难以回收（只能依赖子进程自行退出或 server idle）。

    期望：
    - close(session_id) 会终止 session；
    - close 后继续 write 应返回 not_found（manager 映射为 KeyError）。
    """

    mgr = PersistentExecSessionManager(workspace_root=tmp_path)
    session = mgr.spawn(argv=["python", "-u", "-c", "import time; print('ready'); time.sleep(10)"], cwd=tmp_path)
    assert isinstance(session.session_id, int) and session.session_id >= 1

    r1 = mgr.write(session_id=session.session_id, yield_time_ms=200)
    assert "ready" in r1.stdout
    assert r1.running is True

    mgr.close(session.session_id)

    with pytest.raises(KeyError):
        _ = mgr.write(session_id=session.session_id, yield_time_ms=50)

    # 尽快关闭 runtime server，避免用例间残留后台进程影响稳定性。
    RuntimeClient(workspace_root=tmp_path).call(method="shutdown")

