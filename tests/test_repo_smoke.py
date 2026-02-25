from __future__ import annotations

import os
from pathlib import Path
import sys
import importlib
from importlib.machinery import PathFinder

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_repo_has_agents_md() -> None:
    # 说明：
    # - 开源发布场景下，仓库可能通过 `.gitignore` 将本地协作“宪法”（AGENTS.md）排除在外；
    # - 内部生产/协作场景下，通常要求该文件存在。
    # 因此这里用环境变量显式控制，避免 OSS CI 因“本地文件未入库”而失败。
    if os.environ.get("REQUIRE_LOCAL_DOCS") != "1":
        pytest.skip("local collaboration docs are optional in OSS (set REQUIRE_LOCAL_DOCS=1 to enforce)")
    assert (_repo_root() / "AGENTS.md").exists()


def test_repo_has_docs_index_and_worklog() -> None:
    root = _repo_root()
    if os.environ.get("REQUIRE_LOCAL_DOCS") != "1":
        pytest.skip("local docs index/worklog are optional in OSS (set REQUIRE_LOCAL_DOCS=1 to enforce)")
    assert (root / "DOCS_INDEX.md").exists()
    assert (root / "docs" / "worklog.md").exists()


def test_packages_are_importable_without_install() -> None:
    root = _repo_root()
    sdk_src = root / "packages" / "skills-runtime-sdk-python" / "src"

    sys.path.insert(0, str(sdk_src))

    import skills_runtime  # noqa: F401

    # 说明：旧命名空间 `agent_sdk` 必须“硬断”（tombstone），避免误用。
    # 同时开发机/CI 的 site-packages 可能安装了同名第三方包，所以需要确保解析来源是 repo 的 sdk_src。
    assert PathFinder.find_spec("agent_sdk", [str(sdk_src)]) is not None
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agent_sdk")


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
