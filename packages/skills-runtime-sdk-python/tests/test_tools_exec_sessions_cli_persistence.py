from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_tools_cli(workspace_root: Path, argv: list[str]) -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    src = repo_root / "packages" / "skills-runtime-sdk-python" / "src"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)

    p = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "skills_runtime.cli.main", "tools", *argv],
        cwd=str(workspace_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    assert p.returncode in (0, 20, 21, 22, 23, 24, 25, 26, 27), p.stderr
    assert p.stdout.strip(), p.stderr
    return json.loads(p.stdout)


def test_exec_command_write_stdin_across_processes(tmp_path: Path) -> None:
    """
    回归：`tools exec-command` 与 `tools write-stdin` 在不同进程中也能对同一 session 生效。

    该用例覆盖 BL-004 的核心诉求：CLI 作为常驻会话入口时，需要跨进程持久化 session。
    """

    # 1) 在进程 A 启动交互式 session
    r1 = _run_tools_cli(
        tmp_path,
        [
            "exec-command",
            "--yes",
            "--yield-time-ms",
            "200",
            "--cmd",
            "python -u -c \"import sys; print('ready'); sys.stdout.flush(); line=sys.stdin.readline(); print('got:'+line.strip())\"",
            "--workspace-root",
            str(tmp_path),
        ],
    )
    assert r1["tool"] == "exec_command"
    assert r1["result"]["ok"] is True
    sid = r1["result"]["data"]["session_id"]
    assert isinstance(sid, int) and sid >= 1

    # 2) 在进程 B 写入 stdin 并读取输出
    r2 = _run_tools_cli(
        tmp_path,
        [
            "write-stdin",
            "--yes",
            "--session-id",
            str(sid),
            "--chars",
            # PTY 往往处于 canonical mode；CR 更接近“按下回车”，在不同平台上更稳定。
            "hello\r",
            "--yield-time-ms",
            "400",
            "--workspace-root",
            str(tmp_path),
        ],
    )
    assert r2["tool"] == "write_stdin"
    assert r2["result"]["ok"] is True

    combined = r2["result"]["stdout"]
    running = bool(r2["result"]["data"]["running"])
    # 不同平台/PTY 模式下输出与退出可能分多次 poll 才出现；做有限次数轮询以降低偶发。
    for _ in range(6):
        if not running:
            break
        r3 = _run_tools_cli(
            tmp_path,
            [
                "write-stdin",
                "--yes",
                "--session-id",
                str(sid),
                "--yield-time-ms",
                "400",
                "--workspace-root",
                str(tmp_path),
            ],
        )
        assert r3["result"]["ok"] is True
        combined += r3["result"]["stdout"]
        running = bool(r3["result"]["data"]["running"])

    assert "got:hello" in combined
