from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _extract_marked_python_snippet(*, file_path: Path, begin: str, end: str) -> str:
    """
    从 Markdown 文档中抽取带 begin/end 标记的 Python 片段。

    参数：
    - file_path：Markdown 文件路径
    - begin：开始标记行（包含该行）
    - end：结束标记行（包含该行）

    返回：
    - 片段文本（可直接写入 .py 并执行）
    """

    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if start_idx is None and begin in line:
            start_idx = i
            continue
        if start_idx is not None and end in line:
            end_idx = i
            break

    if start_idx is None or end_idx is None or end_idx <= start_idx:
        raise AssertionError(f"cannot find marked snippet in {file_path}")

    snippet_lines = lines[start_idx : end_idx + 1]
    return "\n".join(snippet_lines).strip() + "\n"


def _run_python_snippet(*, repo_root: Path, snippet: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """
    以子进程运行 Python 片段，并返回 CompletedProcess（stdout/stderr 用于断言与排障）。

    约束：
    - 必须离线可运行：通过 PYTHONPATH 指向 repo 内的 `skills_runtime` 源码。
    - 在临时目录执行，避免污染仓库工作区。
    """

    tmp_path.mkdir(parents=True, exist_ok=True)
    script_path = (tmp_path / "snippet.py").resolve()
    script_path.write_text(snippet, encoding="utf-8")

    env = dict(os.environ)
    src = (repo_root / "packages" / "skills-runtime-sdk-python" / "src").resolve()
    env["PYTHONPATH"] = f"{src}:{env.get('PYTHONPATH', '')}"
    env["PYTHONUTF8"] = "1"

    return subprocess.run(  # noqa: S603
        [sys.executable, str(script_path)],
        cwd=str(tmp_path),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


def test_readme_offline_minimal_example_runs(tmp_path: Path) -> None:
    """
    README 离线最小示例护栏：
    - 代码片段必须可执行（exit code=0）
    - 输出必须包含 final_output 与 wal_locator 作为证据指针
    """

    repo_root = Path(__file__).resolve().parents[1]
    begin = "# BEGIN README_OFFLINE_MINIMAL"
    end = "# END README_OFFLINE_MINIMAL"

    for rel in ("README.md", "README.cn.md"):
        snippet = _extract_marked_python_snippet(file_path=repo_root / rel, begin=begin, end=end)
        p = _run_python_snippet(repo_root=repo_root, snippet=snippet, tmp_path=tmp_path / rel.replace(".", "_"))
        assert p.returncode == 0, (rel, p.stdout, p.stderr)
        assert "final_output=" in p.stdout, (rel, p.stdout, p.stderr)
        assert "wal_locator=" in p.stdout, (rel, p.stdout, p.stderr)


def test_docs_terms_wal_locator_term_is_consistent() -> None:
    """
    关键术语护栏（避免文档口径漂移）：
    - Help API 文档必须说明 wal_locator 的 locator 语义
    - coding-agent 教学材料必须跟随上述口径
    """

    repo_root = Path(__file__).resolve().parents[1]

    help_cn = (repo_root / "help" / "03-sdk-python-api.cn.md").read_text(encoding="utf-8")
    assert "locator" in help_cn
    assert "wal_locator" in help_cn

    cap = (repo_root / "docs_for_coding_agent" / "capability-inventory.md").read_text(encoding="utf-8")
    assert "locator" in cap
    assert "wal_locator" in cap
