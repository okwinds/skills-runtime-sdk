import importlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient


def _repo_root() -> Path:
    """返回仓库根目录。"""

    return Path(__file__).resolve().parents[5]


def _enable_imports() -> None:
    """把 SDK 与 Studio backend 源码目录加入 `sys.path`。"""

    root = _repo_root()
    sdk_src = root / "packages" / "skills-runtime-sdk-python" / "src"
    studio_backend_src = root / "examples" / "studio" / "mvp" / "backend" / "src"
    sys.path.insert(0, str(sdk_src))
    sys.path.insert(0, str(studio_backend_src))


def _load_app(tmp_path: Path, *, fake_backend: bool = False):
    """按给定 workspace_root 重新加载 FastAPI app。"""

    _enable_imports()
    os.environ["STUDIO_WORKSPACE_ROOT"] = str(tmp_path)
    if fake_backend:
        os.environ["STUDIO_LLM_BACKEND"] = "fake"
    else:
        os.environ.pop("STUDIO_LLM_BACKEND", None)
    if "studio_api.app" in sys.modules:
        importlib.reload(sys.modules["studio_api.app"])
    else:
        import studio_api.app  # noqa: F401
    import studio_api.app as mod

    return mod.app


def _client(tmp_path: Path, *, fake_backend: bool = False) -> TestClient:
    """构造测试用 `TestClient`。"""

    return TestClient(_load_app(tmp_path, fake_backend=fake_backend))


def _create_session(client: TestClient) -> str:
    """创建一个最小 session 并返回 session_id。"""

    resp = client.post("/api/v1/sessions", json={"filesystem_sources": []})
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def test_health_omits_workspace_paths(tmp_path: Path) -> None:
    """health 默认不得暴露宿主机绝对路径。"""

    client = _client(tmp_path)

    resp = client.get("/api/v1/health")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert "workspace_root" not in data
    assert "env_file" not in data
    assert isinstance(data.get("env_loaded"), bool)


def test_file_storage_list_sessions_skips_corrupted_session_json(tmp_path: Path) -> None:
    """直接调用 `list_sessions()` 时，单个坏 `session.json` 不得拖垮列表。"""

    _enable_imports()
    from studio_api.storage import FileStorage

    storage = FileStorage(workspace_root=tmp_path)
    good = storage.create_session(title="good", filesystem_sources=[])

    bad_dir = storage.session_dir("sess_bad")
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "session.json").write_text('{"session_id": ', encoding="utf-8")

    sessions = storage.list_sessions()

    ids = {item.session_id for item in sessions}
    assert good.session_id in ids
    assert "sess_bad" not in ids


def test_sessions_api_survives_corrupted_session_json(tmp_path: Path) -> None:
    """Session 列表 API 遇到坏 `session.json` 时仍应返回 200。"""

    client = _client(tmp_path)
    session_id = _create_session(client)
    broken = tmp_path / ".skills_runtime_sdk" / "sessions" / "sess_broken"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "session.json").write_text('{"session_id": ', encoding="utf-8")

    resp = client.get("/api/v1/sessions")

    assert resp.status_code == 200, resp.text
    ids = {item["session_id"] for item in resp.json()["sessions"]}
    assert session_id in ids
    assert "sess_broken" not in ids


def test_sessions_api_survives_semantically_invalid_session_json(tmp_path: Path) -> None:
    """合法 JSON 但字段类型损坏时，session 列表 API 仍应返回 200。"""

    client = _client(tmp_path)
    session_id = _create_session(client)
    session_json = tmp_path / ".skills_runtime_sdk" / "sessions" / session_id / "session.json"
    session_json.write_text(
        (
            '{"session_id":"%s","created_at":"x","updated_at":"x",'
            '"title":null,"runs_count":"oops"}'
        )
        % session_id,
        encoding="utf-8",
    )

    resp = client.get("/api/v1/sessions")

    assert resp.status_code == 200, resp.text
    ids = {item["session_id"] for item in resp.json()["sessions"]}
    assert session_id not in ids


def test_skills_endpoints_return_validation_error_for_corrupted_skills_json(tmp_path: Path) -> None:
    """坏 `skills.json` 必须映射为稳定 `validation_error`。"""

    client = _client(tmp_path)
    session_id = _create_session(client)
    skills_json = tmp_path / ".skills_runtime_sdk" / "sessions" / session_id / "skills.json"
    skills_json.write_text('{"filesystem_sources": ', encoding="utf-8")

    list_resp = client.get(f"/api/v1/sessions/{session_id}/skills")
    set_resp = client.put(
        f"/api/v1/sessions/{session_id}/skills/sources",
        json={"filesystem_sources": [str(tmp_path / "skills")]},
    )
    run_resp = client.post(f"/api/v1/sessions/{session_id}/runs", json={"message": "hello"})

    for resp in (list_resp, set_resp, run_resp):
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["kind"] == "validation_error"


def test_create_run_increments_runs_count(tmp_path: Path) -> None:
    """创建 run 后，session 列表中的 `runs_count` 必须递增。"""

    client = _client(tmp_path, fake_backend=True)
    session_id = _create_session(client)

    before = client.get("/api/v1/sessions")
    assert before.status_code == 200, before.text
    before_item = next(item for item in before.json()["sessions"] if item["session_id"] == session_id)
    assert before_item["runs_count"] == 0

    create = client.post(f"/api/v1/sessions/{session_id}/runs", json={"message": "hello"})
    assert create.status_code == 201, create.text

    after = client.get("/api/v1/sessions")
    assert after.status_code == 200, after.text
    after_item = next(item for item in after.json()["sessions"] if item["session_id"] == session_id)
    assert after_item["runs_count"] == 1


def test_write_run_record_keeps_runs_count_under_concurrency(tmp_path: Path) -> None:
    """并发创建 run 时，`runs_count` 仍应与实际 run 数一致。"""

    _enable_imports()
    from studio_api.storage import FileStorage

    storage = FileStorage(workspace_root=tmp_path)
    session = storage.create_session(title="concurrent", filesystem_sources=[])

    def _worker(i: int) -> None:
        storage.write_run_record(run_id=f"run_{i}", session_id=session.session_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(_worker, range(8)))

    session_json = storage.session_dir(session.session_id) / "session.json"
    session_obj = storage._read_json(session_json)
    assert session_obj["runs_count"] == 8
    assert len([item for item in storage.runs_root().iterdir() if item.is_dir()]) == 8
