from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile
from typing import Any, Dict, Iterable, Mapping

from skills_runtime.core.agent import _sanitize_approval_request
from skills_runtime.safety.approvals import compute_approval_key
from skills_runtime.skills.manager import SkillsManager


TS = "2026-02-07T00:00:00Z"


class FakeRedisClient:
    def __init__(
        self,
        *,
        scan_keys: Iterable[Any],
        hashes: Dict[str, Mapping[str, Any]],
        values: Dict[str, Any],
    ) -> None:
        self._scan_keys = list(scan_keys)
        self._hashes = dict(hashes)
        self._values = dict(values)

    def scan_iter(self, *, match: str):
        for key in self._scan_keys:
            yield key

    def hgetall(self, raw_key: Any) -> Any:
        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
        return self._hashes.get(key, {})

    def get(self, raw_key: Any) -> Any:
        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
        return self._values.get(key)


def _zip_bytes(entries: Dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _mk_manager(tmp_path: Path, *, bundle_bytes: bytes) -> SkillsManager:
    key_prefix = "skills:"
    namespace = "alice:engineering"
    skill_name = "python_testing"

    meta_key = f"{key_prefix}meta:{namespace}:{skill_name}"
    body_key = f"{key_prefix}body:{namespace}:{skill_name}"
    bundle_key = f"{key_prefix}bundle:{namespace}:{skill_name}"

    sha = hashlib.sha256(bundle_bytes).hexdigest()
    meta_hash: Dict[str, Any] = {
        "skill_name": skill_name,
        "description": "d",
        "created_at": TS,
        "metadata": json.dumps(
            {
                "actions": {
                    "noop": {
                        "kind": "shell",
                        "argv": ["bash", "actions/noop.sh"],
                        "timeout_ms": 1234,
                        "env": {"X": "1"},
                    }
                }
            },
            ensure_ascii=False,
        ),
        "body_key": body_key,
        "body_size": 4,
        "bundle_sha256": sha,
        "bundle_format": "zip",
    }
    fake = FakeRedisClient(scan_keys=[meta_key], hashes={meta_key: meta_hash}, values={bundle_key: bundle_bytes})
    mgr = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "namespace": namespace, "sources": ["src-redis"]}],
            "sources": [{"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": key_prefix}}],
            "actions": {"enabled": True},
            "bundles": {"max_bytes": 1024 * 1024, "cache_dir": ".skills_runtime_sdk/bundles"},
        },
        source_clients={"src-redis": fake},
    )
    mgr.scan()
    return mgr


def test_skill_exec_approval_request_binds_to_bundle_sha_and_resolved_argv(tmp_path: Path) -> None:
    bundle_bytes = _zip_bytes({"actions/noop.sh": b"#!/usr/bin/env bash\nexit 0\n", "references/a.txt": b"x\n"})
    sha = hashlib.sha256(bundle_bytes).hexdigest()
    mgr = _mk_manager(tmp_path, bundle_bytes=bundle_bytes)

    summary, req = _sanitize_approval_request(
        "skill_exec",
        args={"skill_mention": "$[alice:engineering].python_testing", "action_id": "noop"},
        skills_manager=mgr,
    )
    assert "skill_exec" in summary
    assert req.get("bundle_sha256") == sha

    argv = req.get("argv")
    assert isinstance(argv, list) and argv
    assert argv[0] == "bash"
    assert isinstance(argv[1], str) and sha in argv[1]
    assert argv[1].endswith("/actions/noop.sh")


def test_skill_exec_approval_key_differs_across_bundle_versions(tmp_path: Path) -> None:
    b1 = _zip_bytes({"actions/noop.sh": b"#!/usr/bin/env bash\nexit 0\n"})
    b2 = _zip_bytes({"actions/noop.sh": b"#!/usr/bin/env bash\necho v2\nexit 0\n"})
    mgr1 = _mk_manager(tmp_path / "w1", bundle_bytes=b1)
    mgr2 = _mk_manager(tmp_path / "w2", bundle_bytes=b2)

    _s1, req1 = _sanitize_approval_request(
        "skill_exec",
        args={"skill_mention": "$[alice:engineering].python_testing", "action_id": "noop"},
        skills_manager=mgr1,
    )
    _s2, req2 = _sanitize_approval_request(
        "skill_exec",
        args={"skill_mention": "$[alice:engineering].python_testing", "action_id": "noop"},
        skills_manager=mgr2,
    )

    k1 = compute_approval_key(tool="skill_exec", request=req1)
    k2 = compute_approval_key(tool="skill_exec", request=req2)
    assert k1 != k2

