from __future__ import annotations

from pathlib import Path
import tomllib


def test_studio_backend_declares_sdk_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = root / "examples" / "studio" / "mvp" / "backend" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = [str(x) for x in data["project"]["dependencies"]]
    assert any(dep.startswith("skills-runtime-sdk") for dep in deps)


def test_studio_backend_declares_uvicorn_runtime_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = root / "examples" / "studio" / "mvp" / "backend" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = [str(x) for x in data["project"]["dependencies"]]
    assert any(dep.startswith("uvicorn") for dep in deps)
