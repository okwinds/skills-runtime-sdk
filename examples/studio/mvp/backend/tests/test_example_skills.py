import importlib
import os
import sys
from pathlib import Path


def _load_app(tmp_path: Path):
    os.environ["STUDIO_WORKSPACE_ROOT"] = str(tmp_path)
    if "studio_api.app" in sys.modules:
        importlib.reload(sys.modules["studio_api.app"])
    else:
        import studio_api.app  # noqa: F401
    import studio_api.app as mod
    return mod.app


def test_example_skills_are_installed_into_workspace_root(tmp_path: Path) -> None:
    _load_app(tmp_path)

    root = tmp_path / ".skills_runtime_sdk" / "skills"
    assert (root / "article-writer" / "SKILL.md").exists()
    assert (root / "novel-writer" / "SKILL.md").exists()

