"""
Bundle cache helpers (Phase 3).

This module intentionally contains small, pure-ish helpers used by SkillsManager and
source implementations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from skills_runtime.skills.bundles import ExtractedBundle
from skills_runtime.skills.models import Skill


def resolve_under_workspace(*, workspace_root: Path, raw: str) -> Path:
    """Resolve a path under workspace root (stable semantics)."""

    p = Path(raw)
    if p.is_absolute():
        return p.resolve()
    return (Path(workspace_root).resolve() / p).resolve()


def bundle_cache_root(*, workspace_root: Path, cache_dir_raw: str) -> Path:
    """bundle extraction cache root (runtime-owned; safe to delete/rebuild)."""

    return resolve_under_workspace(workspace_root=workspace_root, raw=cache_dir_raw)


def is_sha256_hex(value: Any) -> bool:
    """Return True iff value is a 64-char sha256 hex string."""

    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def get_bundle_root_for_tool(
    *,
    skill: Skill,
    purpose: str,
    ensure_redis_bundle_extracted: Callable[..., ExtractedBundle],
) -> Tuple[Path, Optional[str]]:
    """
    Return a bundle root for tools (Phase 3).

    Returns:
    - (bundle_root, bundle_sha256)
    """

    _ = purpose
    if skill.path is not None:
        return Path(skill.path).parent.resolve(), None

    extracted = ensure_redis_bundle_extracted(skill=skill)
    return extracted.bundle_root.resolve(), extracted.bundle_sha256

