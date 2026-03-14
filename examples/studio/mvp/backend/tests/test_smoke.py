import importlib
import os
import sys

from fastapi.testclient import TestClient


def _load_app(tmp_path):
    os.environ["STUDIO_WORKSPACE_ROOT"] = str(tmp_path)
    if "studio_api.app" in sys.modules:
        importlib.reload(sys.modules["studio_api.app"])
    else:
        import studio_api.app  # noqa: F401
    import studio_api.app as mod

    return mod.app


def test_health(tmp_path):
    app = _load_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json().get("ok") is True

