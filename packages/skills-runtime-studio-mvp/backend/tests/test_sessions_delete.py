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


def test_delete_session_removes_it_from_list(tmp_path: Path) -> None:
    app = _load_app(tmp_path)
    client = TestClient(app)

    created = client.post("/api/v1/sessions", json={})
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]

    listed = client.get("/api/v1/sessions")
    assert listed.status_code == 200, listed.text
    ids = [s.get("session_id") for s in listed.json().get("sessions", []) if isinstance(s, dict)]
    assert session_id in ids

    deleted = client.delete(f"/api/v1/sessions/{session_id}")
    assert deleted.status_code == 204, deleted.text

    listed2 = client.get("/api/v1/sessions")
    ids2 = [s.get("session_id") for s in listed2.json().get("sessions", []) if isinstance(s, dict)]
    assert session_id not in ids2

