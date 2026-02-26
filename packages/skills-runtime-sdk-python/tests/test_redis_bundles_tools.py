from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile
from typing import Any, Dict, Iterable, Mapping, Optional

import pytest

from skills_runtime.skills.manager import SkillsManager
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


TS = "2026-02-07T00:00:00Z"


class FakeRedisClient:
    """最小 Redis fake client（覆盖 scan_iter/hgetall/get；可记录调用）。"""

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
        self.scan_calls: list[str] = []
        self.hgetall_calls: list[str] = []
        self.get_calls: list[str] = []

    def _key(self, raw: Any) -> str:
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    def scan_iter(self, *, match: str):
        self.scan_calls.append(match)
        for key in self._scan_keys:
            yield key

    def hgetall(self, raw_key: Any) -> Any:
        key = self._key(raw_key)
        self.hgetall_calls.append(key)
        return self._hashes.get(key, {})

    def get(self, raw_key: Any) -> Any:
        key = self._key(raw_key)
        self.get_calls.append(key)
        return self._values.get(key)


@dataclass
class _FakeCommandResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = 0
    error_kind: str | None = None
    truncated: bool = False
    duration_ms: int = 1


class _RecordingExecutor:
    """记录 run_command 调用的 executor（用于断言 argv/cwd/env/timeout）。"""

    def __init__(self, *, result: Optional[_FakeCommandResult] = None) -> None:
        self.calls: list[dict] = []
        self._result = result or _FakeCommandResult(ok=True, stdout="ok")

    def run_command(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: Optional[Mapping[str, str]] = None,
        timeout_ms: int = 60_000,
        cancel_checker=None,
    ):
        self.calls.append({"argv": list(argv), "cwd": Path(cwd), "env": dict(env or {}), "timeout_ms": int(timeout_ms)})
        return self._result


def _mk_ctx(*, workspace_root: Path, skills_manager: SkillsManager, executor: _RecordingExecutor) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="run_test",
        wal=None,
        executor=executor,
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


def _zip_bytes(entries: Dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _mk_manager_with_redis_skill(tmp_path: Path, *, bundle_bytes: bytes, bundle_sha256: str, extra_meta: dict[str, Any] | None = None) -> tuple[SkillsManager, FakeRedisClient]:
    key_prefix = "skills:"
    namespace = "alice:engineering"
    skill_name = "python_testing"

    meta_key = f"{key_prefix}meta:{namespace}:{skill_name}"
    body_key = f"{key_prefix}body:{namespace}:{skill_name}"
    bundle_key = f"{key_prefix}bundle:{namespace}:{skill_name}"

    fm_metadata = {
        "actions": {
            "noop": {
                "kind": "shell",
                "argv": ["bash", "actions/noop.sh"],
                "timeout_ms": 1234,
                "env": {"X": "1"},
            }
        }
    }
    meta_hash: Dict[str, Any] = {
        "skill_name": skill_name,
        "description": "d",
        "created_at": TS,
        "metadata": json.dumps(fm_metadata, ensure_ascii=False),
        "body_key": body_key,
        "body_size": 4,
        "bundle_sha256": bundle_sha256,
        "bundle_format": "zip",
    }
    if extra_meta:
        meta_hash.update(extra_meta)

    fake = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={meta_key: meta_hash},
        values={body_key: b"body", bundle_key: bundle_bytes},
    )
    mgr = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "namespace": namespace, "sources": ["src-redis"]}],
            "sources": [{"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": key_prefix}}],
            "actions": {"enabled": True},
            "references": {"enabled": True, "allow_assets": False, "default_max_bytes": 65536},
            "bundles": {"max_bytes": 1024 * 1024, "cache_dir": ".skills_runtime_sdk/bundles"},
        },
        source_clients={"src-redis": fake},
    )
    mgr.scan()
    return mgr, fake


def _assert_framework_error_payload(result, *, code: str) -> None:  # type: ignore[no-untyped-def]
    assert result.ok is False
    assert isinstance(result.details, dict)
    data = (result.details or {}).get("data") or {}
    assert isinstance(data, dict)
    err = data.get("error") or {}
    assert isinstance(err, dict)
    assert err.get("code") == code
    assert isinstance(err.get("message"), str) and err.get("message")
    assert isinstance(err.get("details"), dict)


def test_skill_ref_read_redis_bundle_success_and_cache_reuse(tmp_path: Path) -> None:
    bundle_entries = {
        "actions/noop.sh": b"#!/usr/bin/env bash\nexit 0\n",
        "references/a.txt": b"hello\n",
    }
    bundle_bytes = _zip_bytes(bundle_entries)
    sha = hashlib.sha256(bundle_bytes).hexdigest()

    mgr, fake = _mk_manager_with_redis_skill(tmp_path, bundle_bytes=bundle_bytes, bundle_sha256=sha)
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    call = ToolCall(call_id="c1", name="skill_ref_read", args={"skill_mention": "$[alice:engineering].python_testing", "ref_path": "references/a.txt"})
    r1 = skill_ref_read(call, ctx)
    assert r1.ok is True
    assert isinstance(r1.details, dict)
    assert "hello" in str((r1.details or {}).get("stdout") or "")
    assert any(k.endswith(f"bundle:alice:engineering:python_testing") for k in fake.get_calls)

    # second call: should reuse extracted bundle directory and avoid second Redis GET for bundle bytes
    before = list(fake.get_calls)
    r2 = skill_ref_read(call, ctx)
    assert r2.ok is True
    after = list(fake.get_calls)
    assert after == before


def test_redis_scan_is_metadata_only_does_not_fetch_body_or_bundle(tmp_path: Path) -> None:
    bundle_entries = {
        "actions/noop.sh": b"#!/usr/bin/env bash\nexit 0\n",
        "references/a.txt": b"hello\n",
    }
    bundle_bytes = _zip_bytes(bundle_entries)
    sha = hashlib.sha256(bundle_bytes).hexdigest()

    _mgr, fake = _mk_manager_with_redis_skill(tmp_path, bundle_bytes=bundle_bytes, bundle_sha256=sha)
    assert fake.get_calls == [], "scan() must not fetch Redis GET body/bundle bytes (metadata-only)"
    assert fake.hgetall_calls, "scan() must read meta hashes via HGETALL"


def test_injection_reads_body_only_does_not_fetch_bundle(tmp_path: Path) -> None:
    bundle_entries = {
        "actions/noop.sh": b"#!/usr/bin/env bash\nexit 0\n",
        "references/a.txt": b"hello\n",
    }
    bundle_bytes = _zip_bytes(bundle_entries)
    sha = hashlib.sha256(bundle_bytes).hexdigest()

    mgr, fake = _mk_manager_with_redis_skill(tmp_path, bundle_bytes=bundle_bytes, bundle_sha256=sha)
    resolved = mgr.resolve_mentions("$[alice:engineering].python_testing")
    assert resolved
    skill, _m = resolved[0]

    injected = mgr.render_injected_skill(skill, source="test", mention_text="$[alice:engineering].python_testing")
    assert "<skill>" in injected

    assert any("body:alice:engineering:python_testing" in k for k in fake.get_calls)
    assert not any("bundle:alice:engineering:python_testing" in k for k in fake.get_calls)


def test_skill_exec_redis_bundle_materializes_argv_and_sets_stable_env(tmp_path: Path) -> None:
    bundle_entries = {
        "actions/noop.sh": b"#!/usr/bin/env bash\nexit 0\n",
        "references/a.txt": b"hello\n",
    }
    bundle_bytes = _zip_bytes(bundle_entries)
    sha = hashlib.sha256(bundle_bytes).hexdigest()

    mgr, _fake = _mk_manager_with_redis_skill(tmp_path, bundle_bytes=bundle_bytes, bundle_sha256=sha)
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    call = ToolCall(call_id="c1", name="skill_exec", args={"skill_mention": "$[alice:engineering].python_testing", "action_id": "noop"})
    result = skill_exec(call, ctx)
    assert result.ok is True
    assert ex.calls, "shell_exec should have been called via executor"

    argv = ex.calls[0]["argv"]
    assert argv[:2] == ["bash", argv[1]]
    assert "/actions/noop.sh" in argv[1].replace("\\", "/")
    assert sha in argv[1]

    env = ex.calls[0]["env"]
    assert env.get("SKILLS_RUNTIME_SDK_SKILL_MENTION") == "$[alice:engineering].python_testing"
    assert env.get("SKILLS_RUNTIME_SDK_SKILL_ACTION_ID") == "noop"
    assert env.get("SKILLS_RUNTIME_SDK_SKILL_BUNDLE_SHA256") == sha


def test_skill_exec_redis_bundle_missing_fingerprint_fails_closed(tmp_path: Path) -> None:
    bundle_bytes = _zip_bytes({"actions/noop.sh": b"x"})
    sha = hashlib.sha256(bundle_bytes).hexdigest()

    mgr, _fake = _mk_manager_with_redis_skill(tmp_path, bundle_bytes=bundle_bytes, bundle_sha256=sha, extra_meta={"bundle_sha256": None})
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    call = ToolCall(call_id="c1", name="skill_exec", args={"skill_mention": "$[alice:engineering].python_testing", "action_id": "noop"})
    result = skill_exec(call, ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_BUNDLE_FINGERPRINT_MISSING")
    assert ex.calls == []


def test_redis_bundle_invalid_format_fails_closed_via_scan_metadata_invalid(tmp_path: Path) -> None:
    bundle_bytes = _zip_bytes({"actions/noop.sh": b"x"})
    sha = hashlib.sha256(bundle_bytes).hexdigest()

    # bundle_format 非 zip：scan 阶段即应判定 metadata invalid（避免后续工具误用）
    mgr, _fake = _mk_manager_with_redis_skill(
        tmp_path,
        bundle_bytes=bundle_bytes,
        bundle_sha256=sha,
        extra_meta={"bundle_format": "tar"},
    )
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    call = ToolCall(call_id="c1", name="skill_exec", args={"skill_mention": "$[alice:engineering].python_testing", "action_id": "noop"})
    result = skill_exec(call, ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_SCAN_METADATA_INVALID")
    assert ex.calls == []


def test_redis_bundle_size_budget_is_enforced_fail_closed(tmp_path: Path) -> None:
    bundle_entries = {
        "actions/noop.sh": b"#!/usr/bin/env bash\nexit 0\n",
        "references/a.txt": b"hello\n",
    }
    bundle_bytes = _zip_bytes(bundle_entries)
    sha = hashlib.sha256(bundle_bytes).hexdigest()

    key_prefix = "skills:"
    namespace = "alice:engineering"
    skill_name = "python_testing"

    meta_key = f"{key_prefix}meta:{namespace}:{skill_name}"
    body_key = f"{key_prefix}body:{namespace}:{skill_name}"
    bundle_key = f"{key_prefix}bundle:{namespace}:{skill_name}"

    fm_metadata = {
        "actions": {
            "noop": {
                "kind": "shell",
                "argv": ["bash", "actions/noop.sh"],
                "timeout_ms": 1234,
                "env": {"X": "1"},
            }
        }
    }
    meta_hash: Dict[str, Any] = {
        "skill_name": skill_name,
        "description": "d",
        "created_at": TS,
        "metadata": json.dumps(fm_metadata, ensure_ascii=False),
        "body_key": body_key,
        "body_size": 4,
        "bundle_sha256": sha,
        "bundle_format": "zip",
    }
    fake = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={meta_key: meta_hash},
        values={body_key: b"body", bundle_key: bundle_bytes},
    )
    mgr = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "namespace": namespace, "sources": ["src-redis"]}],
            "sources": [{"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": key_prefix}}],
            "actions": {"enabled": True},
            "references": {"enabled": True, "allow_assets": False, "default_max_bytes": 65536},
            "bundles": {"max_bytes": 1, "cache_dir": ".skills_runtime_sdk/bundles"},
        },
        source_clients={"src-redis": fake},
    )
    mgr.scan()

    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    call = ToolCall(call_id="c1", name="skill_ref_read", args={"skill_mention": "$[alice:engineering].python_testing", "ref_path": "references/a.txt"})
    result = skill_ref_read(call, ctx)
    assert result.ok is False
    assert result.error_kind == "permission"
    _assert_framework_error_payload(result, code="SKILL_BUNDLE_TOO_LARGE")


@pytest.mark.parametrize(
    "ref_path",
    [
        "/etc/passwd",
        "../secrets.txt",
        "SKILL.md",
        "references/../SKILL.md",
        "assets/a.txt",
    ],
)
def test_skill_ref_read_redis_bundle_rejects_invalid_ref_path(tmp_path: Path, ref_path: str) -> None:
    bundle_entries = {
        "actions/noop.sh": b"#!/usr/bin/env bash\nexit 0\n",
        "references/a.txt": b"hello\n",
    }
    bundle_bytes = _zip_bytes(bundle_entries)
    sha = hashlib.sha256(bundle_bytes).hexdigest()

    mgr, _fake = _mk_manager_with_redis_skill(tmp_path, bundle_bytes=bundle_bytes, bundle_sha256=sha)
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    call = ToolCall(call_id="c1", name="skill_ref_read", args={"skill_mention": "$[alice:engineering].python_testing", "ref_path": ref_path})
    result = skill_ref_read(call, ctx)
    assert result.ok is False
    assert result.error_kind == "permission"
