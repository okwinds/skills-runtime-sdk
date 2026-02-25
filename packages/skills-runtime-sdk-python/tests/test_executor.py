from __future__ import annotations

import shutil
import sys
from pathlib import Path

from skills_runtime.core.executor import Executor


def _echo_argv() -> list[str]:
    echo_path = Path("/bin/echo")
    if echo_path.exists():
        return [str(echo_path), "hi"]
    # 兼容极端环境：退化为 Python 输出
    return [sys.executable, "-c", 'print("hi")']


def _sleep_argv(seconds: int) -> list[str]:
    sleep_bin = shutil.which("sleep")
    if sleep_bin:
        return [sleep_bin, str(seconds)]
    sleep_path = Path("/bin/sleep")
    if sleep_path.exists():
        return [str(sleep_path), str(seconds)]
    # 兼容极端环境：退化为 Python sleep
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_executor_run_command_echo_ok(tmp_path: Path) -> None:
    ex = Executor()
    result = ex.run_command(_echo_argv(), cwd=tmp_path, env=None, timeout_ms=2_000)

    assert result.ok is True
    assert result.exit_code == 0
    assert result.timeout is False
    assert "hi" in result.stdout


def test_executor_run_command_timeout(tmp_path: Path) -> None:
    ex = Executor()
    result = ex.run_command(_sleep_argv(2), cwd=tmp_path, env=None, timeout_ms=50)

    assert result.ok is False
    assert result.timeout is True
    assert result.error_kind == "timeout"
    assert result.exit_code is None

