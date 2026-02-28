from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from skills_runtime.config.defaults import load_default_config_dict
from skills_runtime.config.loader import load_config_dicts


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "examples" / "apps" / "_shared" / "app_support.py").is_file() and (parent / "packages").is_dir():
            return parent
    raise RuntimeError("repo root not found (expected examples/apps/_shared/app_support.py and packages/)")


def _load_yaml_mapping(path: Path) -> dict:
    obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    if obj is None:
        return {}
    if not isinstance(obj, dict):
        raise TypeError(f"YAML root must be a mapping(dict): {path}")
    return obj


def _load_app_support_module(repo_root: Path) -> Any:
    app_support = repo_root / "examples" / "apps" / "_shared" / "app_support.py"
    spec = importlib.util.spec_from_file_location("examples_apps_app_support", str(app_support))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load module spec for examples/apps/_shared/app_support.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_examples_apps_real_mode_generated_overlay_is_schema_valid(tmp_path: Path) -> None:
    """
    Drift guardrail:
    - examples/apps real-mode overlay must be strict schema-valid under embedded defaults
    - legacy `llm.max_retries` must not appear (use `llm.retry.max_retries`)
    """

    repo_root = _find_repo_root()
    mod = _load_app_support_module(repo_root)

    workspace_root = (tmp_path / "workspace").resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    skills_root = (tmp_path / "skills_root").resolve()
    skills_root.mkdir(parents=True, exist_ok=True)

    overlay_path = mod.write_overlay_for_app(  # type: ignore[attr-defined]
        workspace_root=workspace_root,
        max_steps=3,
        safety_mode="ask",
        enable_references=False,
        enable_actions=False,
        skills_root=skills_root,
        llm_base_url="https://api.openai.com/v1",
        planner_model=None,
        executor_model=None,
    )

    overlay: Dict[str, Any] = _load_yaml_mapping(Path(overlay_path))
    base: Dict[str, Any] = load_default_config_dict()

    llm = overlay.get("llm") or {}
    assert isinstance(llm, dict)
    assert "max_retries" not in llm, "legacy field must not be generated: llm.max_retries"
    retry = llm.get("retry") or {}
    assert isinstance(retry, dict)
    assert retry.get("max_retries") == 2

    try:
        load_config_dicts([base, overlay])
    except Exception as exc:
        pytest.fail(f"examples/apps generated overlay must be schema-valid: {exc}")

