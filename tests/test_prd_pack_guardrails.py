from __future__ import annotations

from pathlib import Path

import pytest


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} is not present in this checkout (docs may be excluded); skipping guardrail")
    return path.read_text(encoding="utf-8")


def test_prd_pack_does_not_reference_codex_folder() -> None:
    """
    PRD pack 护栏：
    - 必须可复刻：不得引用仓库外部路径（例如 ../codex/）
    """

    repo_root = Path(__file__).resolve().parents[1]
    prd_dir = repo_root / "docs" / "prds" / "skills-runtime-sdk-web-mvp"

    files = [
        prd_dir / "README.md",
        prd_dir / "PRD.md",
        prd_dir / "PROMPT_SET.md",
        prd_dir / "EVAL_SPEC.md",
        prd_dir / "PRD_VALIDATION_REPORT.md",
    ]

    combined = "\n".join(_read_text_if_exists(p) for p in files)
    assert "../codex/" not in combined


def test_prd_pack_does_not_claim_unimplemented_product_loops_as_must() -> None:
    """
    PRD pack 护栏（最小）：
    - 不得把当前未实现能力写成“本版 MUST/验收”
    """

    repo_root = Path(__file__).resolve().parents[1]
    prd = _read_text_if_exists(repo_root / "docs" / "prds" / "skills-runtime-sdk-web-mvp" / "PRD.md")
    eval_spec = _read_text_if_exists(repo_root / "docs" / "prds" / "skills-runtime-sdk-web-mvp" / "EVAL_SPEC.md")

    combined = prd + "\n" + eval_spec

    forbidden_must_phrases = [
        "POST /api/v1/sessions/{session_id}/secrets/env",
        "POST /api/v1/runs/{run_id}/cancel",
        "Last-Event-ID",
        "ask_human 的 Web 产品回路",
    ]
    for token in forbidden_must_phrases:
        assert token in combined, "guardrail expects the PRD/Eval to explicitly mention out-of-scope tokens"

    # These tokens are allowed only as "out of scope / not provided / not promised".
    out_of_scope_markers = ["Out of scope", "不提供", "不承诺", "不作为本版", "非本版", "Future / Backlog"]
    assert any(m in combined for m in out_of_scope_markers)


def test_prompt_set_points_to_repo_assets_and_override_fields() -> None:
    """
    Prompt Set 护栏：
    - 必须指向 repo 内 prompt assets
    - 必须写明可覆盖字段（system_path/developer_path）
    """

    repo_root = Path(__file__).resolve().parents[1]
    text = _read_text_if_exists(repo_root / "docs" / "prds" / "skills-runtime-sdk-web-mvp" / "PROMPT_SET.md")

    assert "packages/skills-runtime-sdk-python/src/skills_runtime/assets/prompts/default/" in text
    assert "prompt.system_path" in text
    assert "prompt.developer_path" in text

