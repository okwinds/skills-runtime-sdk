import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _load_app(tmp_path: Path):
    os.environ["STUDIO_WORKSPACE_ROOT"] = str(tmp_path)
    if "studio_api.app" in sys.modules:
        importlib.reload(sys.modules["studio_api.app"])
    else:
        import studio_api.app  # noqa: F401
    import studio_api.app as mod
    return mod.app


def test_new_session_has_default_roots_and_example_skills(tmp_path: Path) -> None:
    app = _load_app(tmp_path)
    client = TestClient(app)

    resp = client.post("/api/v1/sessions", json={})
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["session_id"]

    skills = client.get(f"/api/v1/sessions/{session_id}/skills")
    assert skills.status_code == 200, skills.text
    data = skills.json()

    roots = data.get("roots") or []
    assert isinstance(roots, list)
    assert len(roots) >= 1
    assert str(tmp_path / ".skills_runtime_sdk" / "skills") in roots

    names = [s.get("name") for s in (data.get("skills") or []) if isinstance(s, dict)]
    assert "article-writer" in names
    assert "novel-writer" in names

