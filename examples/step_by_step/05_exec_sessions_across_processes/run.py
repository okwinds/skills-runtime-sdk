"""
exec sessions 跨进程复用示例（离线，可回归）。

实现方式：
- 用 subprocess 多次调用 tools CLI；
- 通过 runtime server 托管 PTY，会话 id 在不同进程间复用。
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def _run_tools_cli(*, repo_root: Path, workspace_root: Path, argv: list[str], timeout_sec: int = 20) -> dict:
    src = repo_root / "packages" / "skills-runtime-sdk-python" / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)

    p = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "agent_sdk.cli.main", "tools", *argv],
        cwd=str(workspace_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_sec,
    )
    if not p.stdout.strip():
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def main() -> int:
    """脚本入口：exec_command → write_stdin（跨进程）。"""

    parser = argparse.ArgumentParser(description="05_exec_sessions_across_processes")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    # repo_root = .../skills-runtime-sdk（本文件位于 examples/step_by_step/<step>/run.py）
    repo_root = Path(__file__).resolve().parents[3]
    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    # 说明：为了避免不同机器上 `python` 指向不同版本/不存在导致示例不稳定，
    # 这里使用当前解释器（sys.executable）作为被启动的交互式进程。
    py = shlex.quote(sys.executable)

    # 1) 进程 A：启动一个会读 stdin 的 python（读到一行就回显）
    r1 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=[
            "exec-command",
            "--yes",
            "--yield-time-ms",
            "200",
            "--cmd",
            f"{py} -u -c \"import sys; print('ready'); sys.stdout.flush(); line=sys.stdin.readline(); print('got:'+line.strip())\"",
            "--workspace-root",
            str(workspace_root),
        ],
    )
    assert r1["tool"] == "exec_command"
    if r1["result"].get("ok") is not True:
        stderr = str(r1["result"].get("stderr") or "")
        # 某些受限环境（例如缺少 /dev/pts 或 PTY 配额很小）无法分配 PTY。
        # 该示例的目标是演示“跨进程复用 session_id”，但在无 PTY 环境下无法成立；
        # 因此仅对“明确的 PTY 不可用”场景做 skip，避免离线回归在 CI 上漂移。
        if "pty" in stderr.lower():
            print(f"[example] skipped: exec_command cannot allocate PTY: {stderr}")
            print("EXAMPLE_OK: step_by_step_05 (SKIPPED_NO_PTY)")
            return 0
        raise AssertionError(stderr)
    sid = r1["result"]["data"]["session_id"]
    assert isinstance(sid, int) and sid >= 1
    print(f"[example] session_id={sid}")

    # 2) 进程 B：写入并轮询输出（输出可能分多次 poll）
    combined = ""
    r2 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=[
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
            str(workspace_root),
        ],
    )
    assert r2["tool"] == "write_stdin"
    if r2["result"]["ok"] is not True:
        raise AssertionError(f"write_stdin failed: {json.dumps(r2, ensure_ascii=False)}")
    combined += r2["result"]["stdout"]
    running = bool(r2["result"]["data"]["running"])

    # 不同平台/PTY 模式下输出与退出可能分多次 poll 才出现；做有限次数轮询以降低偶发。
    for _ in range(6):
        if not running:
            break
        r3 = _run_tools_cli(
            repo_root=repo_root,
            workspace_root=workspace_root,
            argv=[
                "write-stdin",
                "--yes",
                "--session-id",
                str(sid),
                "--yield-time-ms",
                "400",
                "--workspace-root",
                str(workspace_root),
            ],
        )
        assert r3["tool"] == "write_stdin"

        # 兼容极端时序：session 已被工具实现回收后继续 poll，会得到 not_found。
        # 对示例而言，视为“已完成”即可（但仍要求已观测到关键输出）。
        if r3["result"]["ok"] is not True:
            if r3["result"].get("error_kind") == "not_found":
                running = False
                break
            raise AssertionError(f"write_stdin failed: {json.dumps(r3, ensure_ascii=False)}")

        combined += r3["result"]["stdout"]
        running = bool(r3["result"]["data"]["running"])

    assert "got:hello" in combined
    print("[example] combined_stdout_contains=got:hello")
    print("EXAMPLE_OK: step_by_step_05")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
