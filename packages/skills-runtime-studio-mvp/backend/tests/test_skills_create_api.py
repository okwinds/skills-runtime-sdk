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


def _client(tmp_path: Path) -> TestClient:
    return TestClient(_load_app(tmp_path))


def _create_session(client: TestClient) -> str:
    resp = client.post("/api/v1/sessions", json={"filesystem_sources": []})
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _set_sources(client: TestClient, session_id: str, sources: list[str]) -> None:
    resp = client.put(
        f"/api/v1/sessions/{session_id}/skills/sources",
        json={"filesystem_sources": sources},
    )
    assert resp.status_code == 200, resp.text


def test_create_skill_creates_skill_md(tmp_path: Path):
    client = _client(tmp_path)
    session_id = _create_session(client)

    skills_root = tmp_path / "skills"
    _set_sources(client, session_id, [str(skills_root)])

    resp = client.post(
        f"/studio/api/v1/sessions/{session_id}/skills",
        json={"name": "demo_skill", "description": "a demo skill", "body_markdown": "Hello"},
    )
    assert resp.status_code == 201, resp.text

    skill_md = skills_root / "demo_skill" / "SKILL.md"
    assert skill_md.exists()
    text = skill_md.read_text(encoding="utf-8")
    assert "name: demo_skill" in text
    assert "description:" in text

    # storage also lives under STUDIO_WORKSPACE_ROOT
    assert (tmp_path / ".skills_runtime_sdk" / "sessions" / session_id / "session.json").exists()


def test_create_skill_rejects_invalid_name(tmp_path: Path):
    client = _client(tmp_path)
    session_id = _create_session(client)

    skills_root = tmp_path / "skills"
    _set_sources(client, session_id, [str(skills_root)])

    resp = client.post(
        f"/studio/api/v1/sessions/{session_id}/skills",
        json={"name": "Bad Name", "description": "desc"},
    )
    assert resp.status_code == 400
    assert not (skills_root / "Bad Name" / "SKILL.md").exists()


def test_create_skill_rejects_invalid_target_source(tmp_path: Path):
    client = _client(tmp_path)
    session_id = _create_session(client)

    allowed_root = tmp_path / "allowed"
    disallowed_root = tmp_path / "disallowed"
    _set_sources(client, session_id, [str(allowed_root)])

    resp = client.post(
        f"/studio/api/v1/sessions/{session_id}/skills",
        json={"name": "demo_skill", "description": "desc", "target_source": str(disallowed_root)},
    )
    assert resp.status_code == 400
    assert not (allowed_root / "demo_skill" / "SKILL.md").exists()
