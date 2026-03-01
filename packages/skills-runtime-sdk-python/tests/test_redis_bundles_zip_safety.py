from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import stat
import zipfile
from typing import Any, Dict, Iterable, Mapping

import pytest

from skills_runtime.core.errors import FrameworkError
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


def _mk_zip_bytes(entries: Mapping[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _mk_manager(tmp_path: Path, *, bundle_bytes: bytes, bundles_cfg: Dict[str, Any] | None = None) -> SkillsManager:
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
            "bundles": bundles_cfg or {"max_bytes": 1024 * 1024, "cache_dir": ".skills_runtime_sdk/bundles"},
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


def test_extract_zip_bundle_rejects_max_single_file_bytes_budget(tmp_path: Path) -> None:
    from skills_runtime.skills.bundles import extract_zip_bundle_to_dir

    bundle_bytes = _mk_zip_bytes({"references/big.bin": b"x" * 20})
    sha = hashlib.sha256(bundle_bytes).hexdigest()
    dest_dir = tmp_path / "out"
    dest_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(FrameworkError) as exc:
        extract_zip_bundle_to_dir(
            bundle_bytes=bundle_bytes,
            dest_dir=dest_dir,
            expected_sha256=sha,
            max_bytes=1024 * 1024,
            max_single_file_bytes=10,
            max_extracted_bytes=1024,
            max_files=100,
        )
    assert exc.value.code == "SKILL_BUNDLE_TOO_LARGE"
    assert (exc.value.details or {}).get("reason") == "max_single_file_bytes"


def test_extract_zip_bundle_rejects_max_extracted_bytes_budget(tmp_path: Path) -> None:
    from skills_runtime.skills.bundles import extract_zip_bundle_to_dir

    bundle_bytes = _mk_zip_bytes(
        {
            "references/a.bin": b"a" * 8,
            "references/b.bin": b"b" * 8,
        }
    )
    sha = hashlib.sha256(bundle_bytes).hexdigest()
    dest_dir = tmp_path / "out"
    dest_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(FrameworkError) as exc:
        extract_zip_bundle_to_dir(
            bundle_bytes=bundle_bytes,
            dest_dir=dest_dir,
            expected_sha256=sha,
            max_bytes=1024 * 1024,
            max_single_file_bytes=1024,
            max_extracted_bytes=10,
            max_files=100,
        )
    assert exc.value.code == "SKILL_BUNDLE_TOO_LARGE"
    assert (exc.value.details or {}).get("reason") == "max_extracted_bytes"


def test_extract_zip_bundle_rejects_max_files_budget(tmp_path: Path) -> None:
    from skills_runtime.skills.bundles import extract_zip_bundle_to_dir

    bundle_bytes = _mk_zip_bytes(
        {
            "references/a.txt": b"a",
            "references/b.txt": b"b",
            "references/c.txt": b"c",
        }
    )
    sha = hashlib.sha256(bundle_bytes).hexdigest()
    dest_dir = tmp_path / "out"
    dest_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(FrameworkError) as exc:
        extract_zip_bundle_to_dir(
            bundle_bytes=bundle_bytes,
            dest_dir=dest_dir,
            expected_sha256=sha,
            max_bytes=1024 * 1024,
            max_single_file_bytes=1024,
            max_extracted_bytes=1024,
            max_files=2,
        )
    assert exc.value.code == "SKILL_BUNDLE_TOO_LARGE"
    assert (exc.value.details or {}).get("reason") == "max_files"


def test_redis_bundle_extraction_respects_configured_post_extraction_budgets(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 6.3）：
    - post-extraction budgets 必须可通过 config 暴露并生效（而不是只在底层函数可用）。
    """

    bundle_bytes = _mk_zip_bytes({"references/big.bin": b"x" * 20})
    mgr = _mk_manager(
        tmp_path,
        bundle_bytes=bundle_bytes,
        bundles_cfg={
            "max_bytes": 1024 * 1024,
            "cache_dir": ".skills_runtime_sdk/bundles",
            "max_single_file_bytes": 10,
            "max_extracted_bytes": 1024,
            "max_files": 100,
        },
    )
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    call = ToolCall(call_id="c1", name="skill_ref_read", args={"skill_mention": "$[alice:engineering].python_testing", "ref_path": "references/a.txt"})
    result = skill_ref_read(call, ctx)
    _assert_framework_error_payload(result, code_prefix="SKILL_BUNDLE_")
