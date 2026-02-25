#!/usr/bin/env python3
"""
Release guardrail：检查 Git tag 与 Python 包版本是否一致。

用途：
- 在发布工作流（GitHub Actions）中，避免“tag 已更新但包版本未 bump”导致的 PyPI 发布失败。
- 作为本地自检命令，提前发现版本漂移。

规则（默认）：
- tag 形如：v0.1.4.post2 / refs/tags/v0.1.4.post2
- pyproject.toml：[project].version
- __init__.py：__version__ 常量
- 三者必须完全一致（去掉 tag 的前缀 v/refs/tags/ 后再比较）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _normalize_tag(tag: str) -> str:
    raw = tag.strip()
    if raw.startswith("refs/tags/"):
        raw = raw[len("refs/tags/") :]
    if raw.startswith("v"):
        raw = raw[1:]
    return raw


def _read_pyproject_version(pyproject_path: Path) -> str:
    try:
        import tomllib  # py311+
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError(f"missing [project] table in {pyproject_path}")
    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"missing [project].version in {pyproject_path}")
    return version.strip()


_INIT_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']\s*$""", re.MULTILINE)


def _read_init_version(init_path: Path) -> str:
    text = init_path.read_text(encoding="utf-8")
    match = _INIT_VERSION_RE.search(text)
    if not match:
        raise ValueError(f"missing __version__ in {init_path}")
    return match.group(1).strip()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    default_pyproject = repo_root / "packages/skills-runtime-sdk-python/pyproject.toml"
    default_init = repo_root / "packages/skills-runtime-sdk-python/src/skills_runtime/__init__.py"

    parser = argparse.ArgumentParser(description="Check release tag/version alignment (offline guardrail).")
    parser.add_argument("--tag", required=True, help="Git tag name (e.g. v0.1.5 or refs/tags/v0.1.5)")
    parser.add_argument("--pyproject", type=Path, default=default_pyproject, help="Path to pyproject.toml")
    parser.add_argument("--init", dest="init_path", type=Path, default=default_init, help="Path to __init__.py")
    args = parser.parse_args()

    tag_version = _normalize_tag(args.tag)
    try:
        pyproject_version = _read_pyproject_version(args.pyproject)
        init_version = _read_init_version(args.init_path)
    except Exception as e:
        print(f"::error title=release-guardrail::failed to read versions: {e}", file=sys.stderr)
        return 2

    errors: list[str] = []
    if tag_version != pyproject_version:
        errors.append(
            f"tag ({args.tag} -> {tag_version}) != pyproject ([project].version={pyproject_version}) @ {args.pyproject}"
        )
    if pyproject_version != init_version:
        errors.append(f"pyproject (version={pyproject_version}) != __init__ (__version__={init_version}) @ {args.init_path}")

    if errors:
        for line in errors:
            print(f"::error title=release-guardrail::{line}", file=sys.stderr)
        return 1

    print(
        f"[ok] tag/version aligned: tag={args.tag} pyproject={pyproject_version} init={init_version}",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

