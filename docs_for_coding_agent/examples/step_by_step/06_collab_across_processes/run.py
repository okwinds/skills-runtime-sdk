"""
collab primitives 跨进程复用示例（离线，可回归）。

实现方式：
- 用 subprocess 多次调用 tools CLI；
- 通过 runtime server 托管 child agents，id 在不同进程间复用。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _find_repo_root() -> Path:
    """从脚本文件路径向上查找 repo root（包含 `.git` 或 `pyproject.toml`）。"""

    file_value = globals().get("__file__")
    start = Path(file_value).resolve() if file_value else Path.cwd().resolve()
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError(f"repo root not found from {start}")


def _run_tools_cli(*, repo_root: Path, workspace_root: Path, argv: list[str], timeout_sec: int = 20) -> dict:
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
        timeout=timeout_sec,
    )
    if not p.stdout.strip():
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def main() -> int:
    """脚本入口：spawn → send_input → wait（跨进程）。"""

    parser = argparse.ArgumentParser(description="06_collab_across_processes")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    repo_root = _find_repo_root()
    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    r1 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=["spawn-agent", "--workspace-root", str(workspace_root), "--yes", "--message", "wait_input:x"],
    )
    assert r1["tool"] == "spawn_agent"
    assert r1["result"]["ok"] is True
    aid = r1["result"]["data"]["id"]
    assert isinstance(aid, str) and aid
    print(f"[example] agent_id={aid}")

    r2 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=["send-input", "--workspace-root", str(workspace_root), "--yes", "--id", aid, "--message", "ping"],
    )
    assert r2["tool"] == "send_input"
    assert r2["result"]["ok"] is True

    r3 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=["wait", "--workspace-root", str(workspace_root), "--ids", aid, "--timeout-ms", "1500"],
    )
    assert r3["tool"] == "wait"
    assert r3["result"]["ok"] is True
    it = r3["result"]["data"]["results"][0]
    assert it["id"] == aid
    assert it["status"] in {"completed", "running", "failed", "cancelled"}
    if it["status"] == "completed":
        assert it.get("final_output") == "got:ping"

    # 额外：close_agent 路径（状态应为 cancelled）
    r4 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=["spawn-agent", "--workspace-root", str(workspace_root), "--yes", "--message", "wait_input:x"],
    )
    aid2 = r4["result"]["data"]["id"]
    r5 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=["close-agent", "--workspace-root", str(workspace_root), "--yes", "--id", aid2],
    )
    assert r5["result"]["ok"] is True
    r6 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=["wait", "--workspace-root", str(workspace_root), "--ids", aid2, "--timeout-ms", "1500"],
    )
    it2 = r6["result"]["data"]["results"][0]
    assert it2["status"] == "cancelled"

    print("EXAMPLE_OK: step_by_step_06")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
