"""
Plan 与 Human I/O 示例（离线，可回归）。

演示：
- tools update-plan：更新计划（plan step/status）
- tools request-user-input：结构化请求用户输入（用 answers-json 离线注入）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _run_tools_cli(*, repo_root: Path, workspace_root: Path, argv: list[str], timeout_sec: int = 20) -> dict:
    """
    通过子进程调用 tools CLI，并返回 JSON payload。

    参数：
    - repo_root：仓库根目录（用于设置 PYTHONPATH 指向 SDK 源码）
    - workspace_root：workspace 根目录（tools CLI 的执行目录与 workspace_root 参数）
    - argv：tools 子命令 argv（不包含 `tools`）
    - timeout_sec：子进程超时（秒）
    """

    src = repo_root / "packages" / "skills-runtime-sdk-python" / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    env["PYTHONUNBUFFERED"] = "1"

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
    """脚本入口：update_plan → request_user_input（离线答案）。"""

    parser = argparse.ArgumentParser(description="08_plan_and_user_input")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    # repo_root = .../skills-runtime-sdk（本文件位于 examples/step_by_step/<step>/run.py）
    repo_root = Path(__file__).resolve().parents[3]
    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    # 1) update_plan：必须满足“最多一个 in_progress”
    plan_obj = {
        "explanation": "示例：用 update_plan 发出结构化计划（离线）",
        "plan": [
            {"step": "确定验收口径", "status": "completed"},
            {"step": "跑离线回归", "status": "in_progress"},
            {"step": "整理任务总结", "status": "pending"},
        ],
    }
    r1 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=["update-plan", "--input", json.dumps(plan_obj, ensure_ascii=False), "--workspace-root", str(workspace_root)],
    )
    assert r1["tool"] == "update_plan"
    assert r1["result"]["ok"] is True
    assert r1["result"]["data"]["plan"][1]["status"] == "in_progress"
    print("[example] update_plan_ok=1")

    # 2) request_user_input：用 answers-json 注入离线答案，避免交互阻塞
    questions_obj = {
        "questions": [
            {
                "id": "language",
                "header": "语言",
                "question": "文档默认语言？",
                "options": [
                    {"label": "中文", "description": "遵循仓库 AGENTS.md 的默认语言"},
                    {"label": "English", "description": "只在必要场景补充"},
                ],
            },
            {
                "id": "env",
                "header": "环境",
                "question": "示例默认是否离线运行？",
                "options": [
                    {"label": "离线", "description": "Fake backend + deterministic"},
                    {"label": "联网", "description": "需要显式 opt-in（不作为门禁）"},
                ],
            },
        ]
    }
    answers = {"language": "中文", "env": "离线"}
    r2 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=[
            "request-user-input",
            "--input",
            json.dumps(questions_obj, ensure_ascii=False),
            "--answers-json",
            json.dumps(answers, ensure_ascii=False),
            "--workspace-root",
            str(workspace_root),
        ],
    )
    assert r2["tool"] == "request_user_input"
    assert r2["result"]["ok"] is True
    got = {it["id"]: it["answer"] for it in r2["result"]["data"]["answers"]}
    assert got["language"] == "中文"
    assert got["env"] == "离线"
    print("[example] request_user_input_ok=1")

    print("EXAMPLE_OK: step_by_step_08")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

