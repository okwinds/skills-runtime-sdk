from __future__ import annotations

from pathlib import Path


def _repo_root_from_this_test() -> Path:
    # 本文件位于 packages/skills-runtime-sdk-python/tests/ 下。
    return Path(__file__).resolve().parents[3]


def test_examples_run_py_do_not_use_magic_parents_indexing() -> None:
    """
    guardrail：示例入口脚本不应依赖 `Path(...).parents[N]` 这种脆弱的 magic number。
    """

    repo_root = _repo_root_from_this_test()

    run_py_paths = []
    run_py_paths.extend(sorted((repo_root / "examples" / "apps").glob("*/run.py")))
    run_py_paths.extend(sorted((repo_root / "docs_for_coding_agent" / "examples").rglob("run.py")))

    assert run_py_paths, "no example run.py files found (unexpected)"

    offenders: list[str] = []
    for p in run_py_paths:
        text = p.read_text(encoding="utf-8")
        if "parents[" in text:
            offenders.append(str(p.relative_to(repo_root)))

    assert offenders == [], f"found magic parents indexing in: {offenders}"

