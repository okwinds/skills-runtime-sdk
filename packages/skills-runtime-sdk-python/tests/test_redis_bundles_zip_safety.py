from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import stat
import zipfile
from typing import Any, Dict, Iterable, Mapping

import pytest

from skills_runtime.skills.manager import SkillsManager
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


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


def _mk_ctx(*, workspace_root: Path, skills_manager: SkillsManager) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="run_test",
        wal=None,
        executor=None,
        human_io=None,
        env={},
        cancel_checker=None,
        redaction_values=[],
        default_timeout_ms=123,
        max_file_bytes=10,
        sandbox_policy_default="none",
        sandbox_adapter=None,
        emit_tool_events=False,
        event_sink=None,
        skills_manager=skills_manager,
    )


def _mk_zip_bytes_with_symlink() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # normal file
        zf.writestr("references/a.txt", b"ok\n")

        # symlink entry (Unix)
        info = zipfile.ZipInfo("references/link")
        info.create_system = 3  # Unix
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, b"target")
    return buf.getvalue()


def _mk_zip_bytes_with_zip_slip() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("references/../evil.txt", b"nope\n")
    return buf.getvalue()


def _mk_zip_bytes_with_unexpected_top_level() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("misc/x.txt", b"nope\n")
        # keep a valid entry too (should still fail-closed)
        zf.writestr("references/a.txt", b"ok\n")
    return buf.getvalue()


def _mk_manager(tmp_path: Path, *, bundle_bytes: bytes) -> SkillsManager:
    key_prefix = "skills:"
    namespace = "alice:engineering"
    skill_name = "python_testing"
    meta_key = f"{key_prefix}meta:{namespace}:{skill_name}"
    bundle_key = f"{key_prefix}bundle:{namespace}:{skill_name}"

    sha = hashlib.sha256(bundle_bytes).hexdigest()
    meta_hash: Dict[str, Any] = {
        "skill_name": skill_name,
        "description": "d",
        "created_at": TS,
        "metadata": json.dumps({"actions": {}}, ensure_ascii=False),
        "body_key": f"{key_prefix}body:{namespace}:{skill_name}",
        "body_size": 1,
        "bundle_sha256": sha,
        "bundle_format": "zip",
    }
    fake = FakeRedisClient(scan_keys=[meta_key], hashes={meta_key: meta_hash}, values={bundle_key: bundle_bytes})
    mgr = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "namespace": namespace, "sources": ["src-redis"]}],
            "sources": [{"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": key_prefix}}],
            "references": {"enabled": True, "allow_assets": False, "default_max_bytes": 65536},
            "bundles": {"max_bytes": 1024 * 1024, "cache_dir": ".skills_runtime_sdk/bundles"},
        },
        source_clients={"src-redis": fake},
    )
    mgr.scan()
    return mgr


def _assert_framework_error_payload(result, *, code_prefix: str) -> None:  # type: ignore[no-untyped-def]
    assert result.ok is False
    data = ((result.details or {}).get("data") or {}) if isinstance(result.details, dict) else {}
    err = (data.get("error") or {}) if isinstance(data, dict) else {}
    assert isinstance(err, dict)
    code = str(err.get("code") or "")
    assert code.startswith(code_prefix)


@pytest.mark.parametrize(
    "bundle_bytes",
    [
        _mk_zip_bytes_with_zip_slip(),
        _mk_zip_bytes_with_symlink(),
        _mk_zip_bytes_with_unexpected_top_level(),
    ],
)
def test_redis_bundle_zip_safety_rejects_zip_slip_and_symlink(tmp_path: Path, bundle_bytes: bytes) -> None:
    mgr = _mk_manager(tmp_path, bundle_bytes=bundle_bytes)
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    call = ToolCall(call_id="c1", name="skill_ref_read", args={"skill_mention": "$[alice:engineering].python_testing", "ref_path": "references/a.txt"})
    result = skill_ref_read(call, ctx)
    assert result.ok is False
    _assert_framework_error_payload(result, code_prefix="SKILL_BUNDLE_")
