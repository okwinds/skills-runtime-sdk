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


def _extract_backticked_tokens(*, text: str) -> list[str]:
    """
    从 Markdown 文本中抽取所有反引号包裹的 token（`...`）。

    参数：
    - text：Markdown 文本

    返回：
    - tokens：按出现顺序返回，包含重复项
    """

    out: list[str] = []
    start = 0
    while True:
        a = text.find("`", start)
        if a < 0:
            break
        b = text.find("`", a + 1)
        if b < 0:
            break
        out.append(text[a + 1 : b])
        start = b + 1
    return out


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


def test_help_config_sandbox_profile_does_not_claim_custom_value() -> None:
    """
    Help 文档漂移护栏：`sandbox.profile` 允许值必须与实现一致。

    当前实现与 OpenSpec 约束：仅允许 `dev|balanced|prod`，未知值（包含 `custom`）必须 fail-fast。
    因此 Help 不应再宣称 `custom` 是可用值。
    """

    repo_root = Path(__file__).resolve().parents[1]
    for rel in ("help/02-config-reference.md", "help/02-config-reference.cn.md"):
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert "custom|dev|balanced|prod" not in text, rel
        assert "dev/balanced/prod/custom" not in text, rel
        assert "dev|balanced|prod" in text or "dev/balanced/prod" in text, rel


def test_help_cli_runs_metrics_does_not_mention_events_path() -> None:
    """
    Help 文档漂移护栏：`runs metrics` 的参数必须与 CLI 一致。

    约束：
    - CLI 目前支持 `--run-id` 与 `--wal-locator`（filesystem-only）
    - Help 不应出现不存在的 `--events-path`
    """

    repo_root = Path(__file__).resolve().parents[1]
    for rel in ("help/04-cli-reference.md", "help/04-cli-reference.cn.md"):
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert "--events-path" not in text, rel
        assert "--run-id" in text, rel


def test_docs_for_coding_agent_code_entrypoints_exist() -> None:
    """
    docs_for_coding_agent 漂移护栏：Coverage Map / Inventory 中标注的代码入口必须存在。

    说明：
    - 只检查“显式到 .py 文件”的入口，避免对 glob/目录约定做过拟合。
    """

    repo_root = Path(__file__).resolve().parents[1]
    targets = [
        repo_root / "docs_for_coding_agent" / "capability-coverage-map.md",
        repo_root / "docs_for_coding_agent" / "capability-inventory.md",
    ]

    missing: list[tuple[str, str]] = []
    for fp in targets:
        text = fp.read_text(encoding="utf-8")
        for token in _extract_backticked_tokens(text=text):
            if not token.startswith("packages/"):
                continue
            if "*" in token:
                continue
            if not token.endswith(".py"):
                continue
            p = (repo_root / token).resolve()
            if not p.exists():
                missing.append((fp.name, token))

    assert not missing, missing


def test_curated_docs_do_not_reference_nonexistent_examples_workflows_dir() -> None:
    """
    漂移护栏：关键入口文档不应再把 workflows 示例写在 `examples/workflows/`。

    约定：
    - 编码智能体的 workflows 示例位于 `docs_for_coding_agent/examples/workflows/`
    - `examples/` 面向人类应用示例（apps/studio），不提供 workflows 目录
    """

    repo_root = Path(__file__).resolve().parents[1]
    curated = [
        repo_root / "DOCS_INDEX.md",
        repo_root / "docs_for_coding_agent" / "03-workflows-guide.md",
        repo_root / "docs" / "backlog.md",
    ]

    offenders: list[tuple[str, str]] = []
    for fp in curated:
        if not fp.exists():
            continue
        tokens = _extract_backticked_tokens(text=fp.read_text(encoding="utf-8"))
        for t in tokens:
            # 仅禁止旧入口：`examples/workflows/...`
            # 允许新入口：`docs_for_coding_agent/examples/workflows/...`
            if t.startswith("examples/workflows/"):
                offenders.append((str(fp.relative_to(repo_root)), t))

    assert not offenders, offenders
