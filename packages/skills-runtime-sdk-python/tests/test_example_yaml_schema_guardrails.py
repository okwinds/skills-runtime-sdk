from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from skills_runtime.config.defaults import load_default_config_dict
from skills_runtime.config.loader import load_config_dicts


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "help" / "examples").is_dir() and (parent / "packages").is_dir():
            return parent
    raise RuntimeError("repo root not found (expected help/examples and packages/)")


def _load_yaml_mapping(path: Path) -> dict:
    obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    if obj is None:
        return {}
    if not isinstance(obj, dict):
        raise TypeError(f"YAML root must be a mapping(dict): {path}")
    return obj


def _collect_example_overlays(repo_root: Path) -> list[Path]:
    patterns = [
        "help/examples/*.yaml",
        "packages/**/config/*.yaml.example",
    ]
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(repo_root.glob(pattern))
    return sorted(paths)


@pytest.mark.parametrize("overlay_path", _collect_example_overlays(_find_repo_root()))
def test_example_yaml_overlays_are_schema_valid(overlay_path: Path) -> None:
    repo_root = _find_repo_root()
    base = load_default_config_dict()
    overlay = _load_yaml_mapping(overlay_path)

    try:
        load_config_dicts([base, overlay])
    except Exception as exc:
        rel = overlay_path.relative_to(repo_root)
        pytest.fail(f"example overlay YAML must be schema-valid: {rel}: {exc}")
