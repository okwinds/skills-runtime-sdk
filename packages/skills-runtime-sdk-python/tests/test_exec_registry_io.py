from __future__ import annotations

import json
from pathlib import Path

from skills_runtime.runtime.exec_registry_io import read_exec_registry, write_exec_registry


def test_read_exec_registry_returns_fallback_on_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "runtime" / "exec_registry.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{bad json", encoding="utf-8")

    obj = read_exec_registry(exec_registry_path=p, workspace_root=tmp_path)
    assert obj["schema"] == 1
    assert obj["workspace_root"] == str(tmp_path)
    assert obj["exec_sessions"] == {}


def test_write_exec_registry_is_atomic_and_roundtrips(tmp_path: Path) -> None:
    p = tmp_path / "runtime" / "exec_registry.json"
    data = {
        "schema": 1,
        "workspace_root": str(tmp_path),
        "exec_sessions": {"7": {"pid": 123, "marker": "m"}},
        "updated_at_ms": 1,
    }

    write_exec_registry(exec_registry_path=p, obj=data)
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded == data
