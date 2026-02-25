from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_workflow_eval_harness_smoke(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    src = repo_root / "packages" / "skills-runtime-sdk-python" / "src"
    script = (
        repo_root
        / "docs_for_coding_agent"
        / "examples"
        / "workflows"
        / "15_workflow_eval_harness"
        / "run.py"
    )
    assert script.exists()

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(src), str(repo_root)])
    env["PYTHONUNBUFFERED"] = "1"

    workspace_root = (tmp_path / "eval").resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    p = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(script),
            "--workspace-root",
            str(workspace_root),
            "--runs",
            "2",
            "--workflows",
            "04",
        ],
        cwd=str(workspace_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=90,
    )
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert "EXAMPLE_OK:" in p.stdout

    score_path = workspace_root / "eval_score.json"
    assert score_path.exists()
    obj = json.loads(score_path.read_text(encoding="utf-8"))
    assert "04" in obj
    assert 0.0 <= float(obj["04"]["score"]) <= 1.0
