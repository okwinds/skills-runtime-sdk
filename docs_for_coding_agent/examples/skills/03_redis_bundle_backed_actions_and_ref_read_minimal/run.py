"""
离线示例：Redis bundle-backed 的最小闭环（actions + references）。

演示要点：
- 构造 zip bundle bytes（只含 actions/ 与 references/），计算 sha256；
- fake redis 返回 skill metadata（含 bundle_sha256）与 bundle bytes；
- 复用框架内置入口：
  - builtin tool `skill_ref_read`：读取 references/a.txt
  - builtin tool `skill_exec`：执行 actions/noop.sh（通过 fake executor，不实际执行）
- 断言 bundle cache 复用：第二次 ref-read 不触发第二次 Redis GET（bundle bytes）。
"""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile
from typing import Any, Dict, Iterable, Mapping, Optional

from skills_runtime.skills.manager import SkillsManager
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


TS = "2026-02-07T00:00:00Z"


class FakeRedisClient:
    """最小 fake redis client：scan_iter/hgetall/get + 调用记录。"""

    def __init__(self, *, scan_keys: Iterable[Any], hashes: Dict[str, Mapping[str, Any]], values: Dict[str, Any]) -> None:
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


class _FakeCommandResult:
    """fake executor 的返回结构（与 shell_exec 期望字段对齐到最小集合）。"""

    def __init__(self, *, ok: bool, stdout: str = "", stderr: str = "", exit_code: int | None = 0) -> None:
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.error_kind: Optional[str] = None
        self.truncated = False
        self.duration_ms = 1


class _RecordingExecutor:
    """记录 run_command 调用的 executor（避免真实执行 actions）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

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
        return _FakeCommandResult(ok=True, stdout="ok")


def _zip_bytes(entries: Dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _mk_ctx(*, workspace_root: Path, skills_manager: SkillsManager, executor: _RecordingExecutor) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="run_example",
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


def main() -> int:
    """脚本入口：构造 bundle + fake redis → scan → tool 调用 → cache 断言。"""

    parser = argparse.ArgumentParser(description="03_redis_bundle_backed_actions_and_ref_read_minimal")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    namespace = "alice:engineering"
    skill_name = "python-testing-bundle"
    key_prefix = "skills:"

    meta_key = f"{key_prefix}meta:{namespace}:{skill_name}"
    body_key = f"{key_prefix}body:{namespace}:{skill_name}"
    bundle_key = f"{key_prefix}bundle:{namespace}:{skill_name}"

    bundle_entries = {
        "actions/noop.sh": b"#!/usr/bin/env bash\necho noop\nexit 0\n",
        "references/a.txt": b"hello\n",
    }
    bundle_bytes = _zip_bytes(bundle_entries)
    sha = hashlib.sha256(bundle_bytes).hexdigest()

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
        "description": "bundle-backed demo",
        "created_at": TS,
        "required_env_vars": "[]",
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
        workspace_root=workspace_root,
        skills_config={
            "spaces": [{"id": "space-eng", "namespace": namespace, "sources": ["src-redis"]}],
            "sources": [{"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": key_prefix}}],
            "actions": {"enabled": True},
            "references": {"enabled": True, "allow_assets": False, "default_max_bytes": 65536},
            "bundles": {"max_bytes": 1024 * 1024, "cache_dir": ".skills_runtime_sdk/bundles"},
            "scan": {"refresh_policy": "manual", "ttl_sec": 300},
        },
        source_clients={"src-redis": fake},
    )

    report = mgr.scan()
    assert report.errors == [], [e.model_dump() for e in report.errors]
    assert fake.get_calls == [], "scan() must be metadata-only (must not fetch body/bundle bytes)"

    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=workspace_root, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read
    from skills_runtime.tools.builtin.skill_exec import skill_exec

    mention = f"$[{namespace}].{skill_name}"

    call_ref = ToolCall(call_id="c1", name="skill_ref_read", args={"skill_mention": mention, "ref_path": "references/a.txt"})
    r1 = skill_ref_read(call_ref, ctx)
    assert r1.ok is True
    assert isinstance(r1.details, dict)
    assert "hello" in str((r1.details or {}).get("stdout") or "")
    assert any(k.endswith(f"bundle:{namespace}:{skill_name}") for k in fake.get_calls), fake.get_calls

    # second ref-read should reuse extracted cache dir and avoid second Redis GET(bundle)
    before = list(fake.get_calls)
    r2 = skill_ref_read(call_ref, ctx)
    assert r2.ok is True
    after = list(fake.get_calls)
    assert after == before, {"before": before, "after": after}

    call_exec = ToolCall(call_id="c2", name="skill_exec", args={"skill_mention": mention, "action_id": "noop"})
    r3 = skill_exec(call_exec, ctx)
    assert r3.ok is True
    assert ex.calls, "skill_exec should have invoked executor (shell_exec) once"

    print("EXAMPLE_OK: redis_bundle_backed_actions_ref_read")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

