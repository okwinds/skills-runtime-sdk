"""
离线示例：Redis bundle-backed 的 fail-closed 失败语义（failure semantics）。

覆盖要点（都通过工具接口触发）：
- A) 非法 zip entry：bundle 解压阶段必须 fail-closed（返回 SKILL_BUNDLE_INVALID）
- B) 非法 ref_path：必须拒绝（permission，SKILL_REF_PATH_INVALID）
- C) 非法 action argv：必须拒绝（permission，SKILL_ACTION_ARGV_PATH_ESCAPE），且不得调用 executor

断言口径（稳定字段）：
- result.error_kind
- result.details["data"]["error"]["code"]
- 可选：对 bundle zip 校验分支额外断言 data.error.details.reason
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
from skills_runtime.tools.protocol import ToolCall, ToolResult
from skills_runtime.tools.registry import ToolExecutionContext


TS = "2026-02-07T00:00:00Z"


class FakeRedisClient:
    """最小 fake redis client：scan_iter/hgetall/get + 调用记录（用于断言 scan metadata-only）。"""

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

    def __init__(
        self,
        *,
        ok: bool,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = 0,
        error_kind: str | None = None,
    ) -> None:
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.error_kind = error_kind
        self.truncated = False
        self.duration_ms = 1


class _RecordingExecutor:
    """记录 run_command 调用的 executor（用于断言 fail-closed 时不应触发执行）。"""

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


def _err(result: ToolResult) -> dict:
    assert result.ok is False
    assert isinstance(result.details, dict)
    data = (result.details or {}).get("data") or {}
    assert isinstance(data, dict)
    err = data.get("error") or {}
    assert isinstance(err, dict)
    return err


def _assert_error_code(result: ToolResult, *, code: str, error_kind: str | None = None) -> dict:
    if error_kind is not None:
        assert result.error_kind == error_kind, {"expected": error_kind, "actual": result.error_kind}
    err = _err(result)
    assert err.get("code") == code, {"expected": code, "actual": err.get("code"), "message": err.get("message")}
    return err


def main() -> int:
    """脚本入口：构造 3 个 bundle-backed skills → 分别触发 3 类 fail-closed 分支并断言。"""

    parser = argparse.ArgumentParser(description="04_redis_bundle_backed_failure_semantics_minimal")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    namespace = "alice:engineering"
    key_prefix = "skills:"

    # A) 非法 zip entry：包含 references/../evil.txt（dotdot_segment），同时放一个合法文件用于证明 fail-closed
    skill_invalid_bundle = "python-testing-invalid-bundle"
    bundle_invalid = _zip_bytes(
        {
            "references/../evil.txt": b"nope\n",
            "references/a.txt": b"ok\n",
        }
    )
    sha_invalid = hashlib.sha256(bundle_invalid).hexdigest()

    # B) 非法 ref_path：bundle 本身合法，但 ref_path 触碰边界必须 fail-closed
    skill_ref_path_invalid = "python-testing-ref-path-invalid"
    bundle_ok = _zip_bytes({"references/a.txt": b"hello\n"})
    sha_ok = hashlib.sha256(bundle_ok).hexdigest()

    # C) 非法 action argv：绝对路径（/etc/passwd）必须 fail-closed，且不触发 executor
    skill_action_argv_escape = "python-testing-action-argv-escape"
    bundle_ok2 = _zip_bytes({"references/a.txt": b"hello\n"})
    sha_ok2 = hashlib.sha256(bundle_ok2).hexdigest()
    fm_bad_action = {"actions": {"pwn": {"kind": "shell", "argv": ["bash", "/etc/passwd"]}}}

    def _meta_hash(*, skill_name: str, sha: str, frontmatter_metadata: dict) -> Dict[str, Any]:
        body_key = f"{key_prefix}body:{namespace}:{skill_name}"
        return {
            "skill_name": skill_name,
            "description": "bundle-backed failure semantics demo",
            "created_at": TS,
            "required_env_vars": "[]",
            "metadata": json.dumps(frontmatter_metadata, ensure_ascii=False),
            "body_key": body_key,
            "body_size": 4,
            "bundle_sha256": sha,
            "bundle_format": "zip",
        }

    meta_key_a = f"{key_prefix}meta:{namespace}:{skill_invalid_bundle}"
    meta_key_b = f"{key_prefix}meta:{namespace}:{skill_ref_path_invalid}"
    meta_key_c = f"{key_prefix}meta:{namespace}:{skill_action_argv_escape}"
    bundle_key_a = f"{key_prefix}bundle:{namespace}:{skill_invalid_bundle}"
    bundle_key_b = f"{key_prefix}bundle:{namespace}:{skill_ref_path_invalid}"
    bundle_key_c = f"{key_prefix}bundle:{namespace}:{skill_action_argv_escape}"

    meta_hashes: Dict[str, Mapping[str, Any]] = {
        meta_key_a: _meta_hash(skill_name=skill_invalid_bundle, sha=sha_invalid, frontmatter_metadata={"actions": {}}),
        meta_key_b: _meta_hash(skill_name=skill_ref_path_invalid, sha=sha_ok, frontmatter_metadata={"actions": {}}),
        meta_key_c: _meta_hash(skill_name=skill_action_argv_escape, sha=sha_ok2, frontmatter_metadata=fm_bad_action),
    }
    values: Dict[str, Any] = {
        bundle_key_a: bundle_invalid,
        bundle_key_b: bundle_ok,
        bundle_key_c: bundle_ok2,
        # scan 必须 metadata-only；body_key 只作为结构完整性兜底（不应被读取）
        f"{key_prefix}body:{namespace}:{skill_invalid_bundle}": b"body",
        f"{key_prefix}body:{namespace}:{skill_ref_path_invalid}": b"body",
        f"{key_prefix}body:{namespace}:{skill_action_argv_escape}": b"body",
    }

    fake = FakeRedisClient(scan_keys=[meta_key_a, meta_key_b, meta_key_c], hashes=meta_hashes, values=values)

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

    from skills_runtime.tools.builtin.skill_exec import skill_exec
    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=workspace_root, skills_manager=mgr, executor=ex)

    # A) 非法 zip entry（dotdot_segment）
    mention_a = f"$[{namespace}].{skill_invalid_bundle}"
    r_a = skill_ref_read(
        ToolCall(call_id="c_a", name="skill_ref_read", args={"skill_mention": mention_a, "ref_path": "references/a.txt"}),
        ctx,
    )
    err_a = _assert_error_code(r_a, code="SKILL_BUNDLE_INVALID")
    # 可选：reason 断言（示例固定为 dotdot_segment）
    assert (err_a.get("details") or {}).get("reason") in {"dotdot_segment", "unexpected_top_level"}, err_a

    # B) 非法 ref_path（包含 ..）
    mention_b = f"$[{namespace}].{skill_ref_path_invalid}"
    r_b = skill_ref_read(
        ToolCall(call_id="c_b", name="skill_ref_read", args={"skill_mention": mention_b, "ref_path": "references/../x"}),
        ctx,
    )
    _assert_error_code(r_b, code="SKILL_REF_PATH_INVALID", error_kind="permission")

    # C) 非法 action argv（绝对路径）
    mention_c = f"$[{namespace}].{skill_action_argv_escape}"
    before_calls = list(ex.calls)
    r_c = skill_exec(ToolCall(call_id="c_c", name="skill_exec", args={"skill_mention": mention_c, "action_id": "pwn"}), ctx)
    _assert_error_code(r_c, code="SKILL_ACTION_ARGV_PATH_ESCAPE", error_kind="permission")
    assert ex.calls == before_calls, "argv 校验失败必须 fail-closed，不应触发 executor"

    print("EXAMPLE_OK: redis_bundle_backed_failure_semantics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
