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


def test_spawn_send_wait_across_processes(tmp_path: Path) -> None:
    """
    回归：collab tools 在不同进程间可复用同一 child agent id。

    该用例覆盖 BL-005 的核心诉求：spawn/send_input/wait 在 CLI 多次调用时不丢失子 agent 状态。
    """

    r1 = _run_tools_cli(tmp_path, ["spawn-agent", "--workspace-root", str(tmp_path), "--yes", "--message", "wait_input:x"])
    assert r1["tool"] == "spawn_agent"
    assert r1["result"]["ok"] is True
    aid = r1["result"]["data"]["id"]
    assert isinstance(aid, str) and aid

    r_waiting = _run_tools_cli(tmp_path, ["wait", "--workspace-root", str(tmp_path), "--ids", aid, "--timeout-ms", "100"])
    assert r_waiting["tool"] == "wait"
    assert r_waiting["result"]["ok"] is True
    waiting_item = r_waiting["result"]["data"]["results"][0]
    assert waiting_item["id"] == aid
    assert waiting_item["status"] == "waiting_human"

    r2 = _run_tools_cli(tmp_path, ["send-input", "--workspace-root", str(tmp_path), "--yes", "--id", aid, "--message", "ping"])
    assert r2["tool"] == "send_input"
    assert r2["result"]["ok"] is True

    r3 = _run_tools_cli(tmp_path, ["wait", "--workspace-root", str(tmp_path), "--ids", aid, "--timeout-ms", "1500"])
    assert r3["tool"] == "wait"
    assert r3["result"]["ok"] is True
    it = r3["result"]["data"]["results"][0]
    assert it["id"] == aid
    assert it["status"] in {"completed", "running", "failed", "cancelled"}
    if it["status"] == "completed":
        assert it.get("final_output") == "got:ping"


def test_spawn_resume_reports_waiting_human_across_processes(tmp_path: Path) -> None:
    """
    回归：runtime-backed collab child 在等待输入时，resume-agent 必须显式返回 waiting_human。
    """

    r1 = _run_tools_cli(tmp_path, ["spawn-agent", "--workspace-root", str(tmp_path), "--yes", "--message", "wait_input:x"])
    assert r1["tool"] == "spawn_agent"
    assert r1["result"]["ok"] is True
    aid = r1["result"]["data"]["id"]
    assert isinstance(aid, str) and aid

    r2 = _run_tools_cli(tmp_path, ["resume-agent", "--workspace-root", str(tmp_path), "--yes", "--id", aid])
    assert r2["tool"] == "resume_agent"
    assert r2["result"]["ok"] is True
    assert r2["result"]["data"]["id"] == aid
    assert r2["result"]["data"]["status"] == "waiting_human"


def test_waiting_human_visible_and_resumable_across_processes(tmp_path: Path) -> None:
    """
    回归：wait_input child 在跨进程场景应显式暴露 waiting_human，并可被 send_input 恢复。
    """

    r1 = _run_tools_cli(tmp_path, ["spawn-agent", "--workspace-root", str(tmp_path), "--yes", "--message", "wait_input:x"])
    aid = r1["result"]["data"]["id"]

    # 先观察等待态（允许极短竞态，最多重试几次）
    status = None
    for _ in range(5):
        r_wait = _run_tools_cli(tmp_path, ["wait", "--workspace-root", str(tmp_path), "--ids", aid, "--timeout-ms", "80"])
        assert r_wait["result"]["ok"] is True
        status = r_wait["result"]["data"]["results"][0]["status"]
        if status == "waiting_human":
            break
    assert status == "waiting_human"

    r_resume = _run_tools_cli(tmp_path, ["resume-agent", "--workspace-root", str(tmp_path), "--yes", "--id", aid])
    assert r_resume["tool"] == "resume_agent"
    assert r_resume["result"]["ok"] is True
    assert r_resume["result"]["data"]["status"] == "waiting_human"

    r_send = _run_tools_cli(tmp_path, ["send-input", "--workspace-root", str(tmp_path), "--yes", "--id", aid, "--message", "hello"])
    assert r_send["result"]["ok"] is True

    r_done = _run_tools_cli(tmp_path, ["wait", "--workspace-root", str(tmp_path), "--ids", aid, "--timeout-ms", "1500"])
    assert r_done["result"]["ok"] is True
    it = r_done["result"]["data"]["results"][0]
    assert it["status"] == "completed"
    assert it.get("final_output") == "got:hello"


def test_spawn_close_wait_across_processes(tmp_path: Path) -> None:
    """
    回归（BL-005 完整性）：close_agent 应能跨进程取消 child，并在 wait 中可观测为 cancelled。
    """

    r1 = _run_tools_cli(tmp_path, ["spawn-agent", "--workspace-root", str(tmp_path), "--yes", "--message", "wait_input:x"])
    assert r1["tool"] == "spawn_agent"
    assert r1["result"]["ok"] is True
    aid = r1["result"]["data"]["id"]
    assert isinstance(aid, str) and aid

    r2 = _run_tools_cli(tmp_path, ["close-agent", "--workspace-root", str(tmp_path), "--yes", "--id", aid])
    assert r2["tool"] == "close_agent"
    assert r2["result"]["ok"] is True

    r3 = _run_tools_cli(tmp_path, ["wait", "--workspace-root", str(tmp_path), "--ids", aid, "--timeout-ms", "1500"])
    assert r3["tool"] == "wait"
    assert r3["result"]["ok"] is True
    it = r3["result"]["data"]["results"][0]
    assert it["id"] == aid
    assert it["status"] == "cancelled"


def test_spawn_close_send_input_fails_across_processes(tmp_path: Path) -> None:
    r1 = _run_tools_cli(tmp_path, ["spawn-agent", "--workspace-root", str(tmp_path), "--yes", "--message", "wait_input:x"])
    aid = r1["result"]["data"]["id"]

    r2 = _run_tools_cli(tmp_path, ["close-agent", "--workspace-root", str(tmp_path), "--yes", "--id", aid])
    assert r2["result"]["ok"] is True

    r3 = _run_tools_cli(tmp_path, ["send-input", "--workspace-root", str(tmp_path), "--yes", "--id", aid, "--message", "late"])
    assert r3["tool"] == "send_input"
    assert r3["result"]["ok"] is False
    assert r3["result"]["error_kind"] == "not_found"


def test_spawn_close_resume_is_validation_across_processes(tmp_path: Path) -> None:
    r1 = _run_tools_cli(tmp_path, ["spawn-agent", "--workspace-root", str(tmp_path), "--yes", "--message", "wait_input:x"])
    aid = r1["result"]["data"]["id"]

    r2 = _run_tools_cli(tmp_path, ["close-agent", "--workspace-root", str(tmp_path), "--yes", "--id", aid])
    assert r2["result"]["ok"] is True

    r3 = _run_tools_cli(tmp_path, ["resume-agent", "--workspace-root", str(tmp_path), "--yes", "--id", aid])
    assert r3["tool"] == "resume_agent"
    assert r3["result"]["ok"] is False
    assert r3["result"]["error_kind"] == "validation"
