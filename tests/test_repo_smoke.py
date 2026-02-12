from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_repo_has_agents_md() -> None:
    assert (_repo_root() / "AGENTS.md").exists()


def test_repo_has_docs_index_and_worklog() -> None:
    root = _repo_root()
    assert (root / "DOCS_INDEX.md").exists()
    assert (root / "docs" / "worklog.md").exists()


def test_packages_are_importable_without_install() -> None:
    root = _repo_root()
    sdk_src = root / "packages" / "skills-runtime-sdk-python" / "src"

    sys.path.insert(0, str(sdk_src))

    import agent_sdk  # noqa: F401


def test_legacy_web_mvp_is_importable_when_enabled() -> None:
    """
    legacy 归档内容默认不参与主线回归；仅在显式启用时做最小 import 自检。
    """

    if os.environ.get("INCLUDE_LEGACY") != "1":
        pytest.skip("legacy import check is disabled (set INCLUDE_LEGACY=1 to enable)")

    root = _repo_root()
    web_src = root / "legacy" / "skills-runtime-sdk-web-mvp-python" / "src"
    if not web_src.exists():
        pytest.skip("legacy web mvp not present")

    sys.path.insert(0, str(web_src))
    import agent_sdk_web_mvp  # noqa: F401
