from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Iterable, Mapping, Optional

import pytest

from skills_runtime.core.errors import FrameworkError
from skills_runtime.skills.manager import SkillsManager


TS = "2026-02-07T00:00:00Z"


@dataclass
class CountingRedisClient:
    """
    Redis fake client：用 scan_iter 作为“发生 I/O 的计数点”。

    约束：
    - scan 阶段必须 metadata-only：不得调用 get()。
    """

    keys: list[str]
    meta_by_key: dict[str, Mapping[str, Any]]
    bodies: dict[str, Any]
    scan_error: Optional[Exception] = None
    hgetall_error: Optional[Exception] = None
    scan_delay_sec: float = 0.0

    def __post_init__(self) -> None:
        self.scan_calls: list[str] = []
        self.get_calls: list[str] = []
        self.hgetall_calls: list[str] = []

    def scan_iter(self, *, match: str) -> Iterable[Any]:
        self.scan_calls.append(match)
        if self.scan_delay_sec > 0:
            time.sleep(self.scan_delay_sec)
        if self.scan_error is not None:
            raise self.scan_error
        for k in self.keys:
            yield k

    def hgetall(self, raw_key: Any) -> Any:
        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
        self.hgetall_calls.append(key)
        if self.hgetall_error is not None:
            raise self.hgetall_error
        return dict(self.meta_by_key.get(key, {}))

    def get(self, raw_key: Any) -> Any:
        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
        self.get_calls.append(key)
        return self.bodies.get(key)


def _mk_manager(tmp_path: Path, redis_client: CountingRedisClient, *, scan: dict[str, Any]) -> SkillsManager:
    """构造带 redis source 的 SkillsManager（注入 fake client，避免 DSN/依赖）。"""

    cfg: dict[str, Any] = {
        "spaces": [{"id": "space-eng", "namespace": "alice:engineering", "sources": ["src-redis"]}],
        "sources": [{"id": "src-redis", "type": "redis", "options": {"key_prefix": "skills:"}}],
        "scan": dict(scan),
    }
    return SkillsManager(workspace_root=tmp_path, skills_config=cfg, source_clients={"src-redis": redis_client})


def _redis_fixture_client(*, skill_name: str = "python_testing") -> CountingRedisClient:
    meta_key = f"skills:meta:alice:engineering:{skill_name}"
    body_key = f"skills:body:alice:engineering:{skill_name}"
    return CountingRedisClient(
        keys=[meta_key],
        meta_by_key={
            meta_key: {
                "skill_name": skill_name,
                "description": "pytest patterns",
                "created_at": TS,
                "required_env_vars": "[]",
                "metadata": "{}",
                "body_key": body_key,
            }
        },
        bodies={body_key: "# BODY\n"},
    )


def test_refresh_policy_always_scan_is_not_cached(tmp_path: Path) -> None:
    """RP-001: always - 连续 scan 两次必须触发两次 source scan。"""

    client = _redis_fixture_client()
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "always", "ttl_sec": 300})

    mgr.scan()
    mgr.scan()

    assert len(client.scan_calls) == 2


def test_refresh_policy_ttl_first_scan_builds_cache(tmp_path: Path) -> None:
    """RP-002: ttl - 第一次 scan 触发一次 source scan。"""

    client = _redis_fixture_client()
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "ttl", "ttl_sec": 300})

    mgr.scan()
    assert len(client.scan_calls) == 1


def test_refresh_policy_ttl_scan_within_ttl_reuses_cache(tmp_path: Path) -> None:
    """RP-003: ttl - TTL 内再次 scan 不触发 source scan。"""

    client = _redis_fixture_client()
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "ttl", "ttl_sec": 300})

    mgr.scan()
    mgr.scan()

    assert len(client.scan_calls) == 1


def test_refresh_policy_ttl_scan_after_expire_refreshes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RP-004: ttl - TTL 过期后 scan 触发刷新。"""

    client = _redis_fixture_client()
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "ttl", "ttl_sec": 10})

    t = {"now": 100.0}

    def fake_now() -> float:
        return float(t["now"])

    monkeypatch.setattr(SkillsManager, "_now_monotonic", staticmethod(fake_now), raising=True)

    mgr.scan()
    t["now"] = 200.0
    mgr.scan()

    assert len(client.scan_calls) == 2


def test_refresh_policy_ttl_concurrent_expired_scan_singleflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RP-005: ttl - TTL 过期时并发两次 scan，只允许 1 次刷新 I/O。"""

    client = _redis_fixture_client()
    client.scan_delay_sec = 0.2
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "ttl", "ttl_sec": 10})

    t = {"now": 100.0}

    def fake_now() -> float:
        return float(t["now"])

    monkeypatch.setattr(SkillsManager, "_now_monotonic", staticmethod(fake_now), raising=True)

    mgr.scan()
    t["now"] = 200.0

    results: list[object] = []

    def worker() -> None:
        results.append(mgr.scan())

    th1 = threading.Thread(target=worker)
    th2 = threading.Thread(target=worker)
    th1.start()
    time.sleep(0.05)
    th2.start()
    th1.join(timeout=5)
    th2.join(timeout=5)

    assert len(results) == 2
    assert len(client.scan_calls) == 2  # 初始 1 次 + 并发刷新 1 次


def test_refresh_policy_ttl_refresh_failed_without_cache_returns_errors(tmp_path: Path) -> None:
    """RP-006: ttl - 刷新失败且无历史缓存：scan 必须失败（errors 非空）。"""

    client = _redis_fixture_client()
    client.scan_error = RuntimeError("scan boom")
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "ttl", "ttl_sec": 300})

    report = mgr.scan()
    assert any(e.code == "SKILL_SCAN_SOURCE_UNAVAILABLE" for e in report.errors)
    assert len(client.scan_calls) == 1


def test_refresh_policy_ttl_refresh_failed_with_cache_returns_old_cache_and_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RP-007: ttl - 刷新失败但有历史缓存：返回旧缓存 + warnings。"""

    client = _redis_fixture_client()
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "ttl", "ttl_sec": 10})

    t = {"now": 100.0}

    def fake_now() -> float:
        return float(t["now"])

    monkeypatch.setattr(SkillsManager, "_now_monotonic", staticmethod(fake_now), raising=True)

    first = mgr.scan()
    assert first.errors == []
    assert len(first.skills) == 1

    # 过期后，刷新失败
    t["now"] = 200.0
    client.scan_error = RuntimeError("scan boom")
    second = mgr.scan()

    assert len(second.skills) == 1
    assert second.skills[0].skill_name == "python_testing"
    assert second.errors == []  # 旧缓存成功结果，不应被 errors 覆盖
    assert any(w.code == "SKILL_SCAN_REFRESH_FAILED" for w in second.warnings)
    assert len(client.scan_calls) == 2


def test_refresh_policy_manual_scan_is_cached_after_first(tmp_path: Path) -> None:
    """RP-008/RP-009: manual - 第一次 scan 写入缓存；再次 scan 不自动刷新。"""

    client = _redis_fixture_client()
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "manual", "ttl_sec": 300})

    mgr.scan()
    mgr.scan()

    assert len(client.scan_calls) == 1


def test_refresh_policy_manual_refresh_api_triggers_rescan(tmp_path: Path) -> None:
    """RP-010: manual - 显式 refresh() 必须触发刷新。"""

    client = _redis_fixture_client()
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "manual", "ttl_sec": 300})

    mgr.scan()
    mgr.refresh()

    assert len(client.scan_calls) == 2


def test_refresh_policy_cache_key_changes_when_scan_options_change(tmp_path: Path) -> None:
    """RP-011: ttl/manual - 影响 scan 行为的配置变化时不得复用旧缓存。"""

    client = _redis_fixture_client()
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "ttl", "ttl_sec": 300, "max_depth": 10})

    mgr.scan()
    assert len(client.scan_calls) == 1

    # 直接修改 manager 的 scan options（模拟配置变化）；下一次 scan 必须触发刷新。
    mgr._scan_options["max_depth"] = 1  # type: ignore[attr-defined]
    mgr.scan()
    assert len(client.scan_calls) == 2


def test_refresh_policy_cache_is_metadata_only_never_reads_body_on_scan(tmp_path: Path) -> None:
    """RP-012: scan 缓存与返回必须是 metadata-only：scan 期间不得触发 get()。"""

    client = _redis_fixture_client()
    mgr = _mk_manager(tmp_path, client, scan={"refresh_policy": "ttl", "ttl_sec": 300})

    mgr.scan()
    mgr.scan()

    assert client.get_calls == []


def _write_fs_skill(root: Path, *, name: str) -> None:
    """写入一个最小 filesystem skill。"""

    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(["---", f"name: {name}", 'description: "d"', "---", "# body", ""]),
        encoding="utf-8",
    )


def _mk_fs_manager(tmp_path: Path, skills_root: Path, *, scan: dict[str, Any]) -> SkillsManager:
    """构造 filesystem source 的 SkillsManager（用于验证 resolve 前刷新语义）。"""

    cfg: dict[str, Any] = {
        "spaces": [{"id": "space-eng", "namespace": "alice:engineering", "sources": ["src-fs"]}],
        "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
        "scan": dict(scan),
    }
    return SkillsManager(workspace_root=tmp_path, skills_config=cfg)


def test_resolve_mentions_default_always_sees_new_filesystem_skills_immediately(tmp_path: Path) -> None:
    """默认 always：resolve_mentions 前应刷新，使新增 skill 立即可见。"""

    root = tmp_path / "skills_root"
    _write_fs_skill(root, name="s1")
    mgr = _mk_fs_manager(tmp_path, root, scan={"refresh_policy": "always", "ttl_sec": 300})

    # 第一次解析 OK
    mgr.resolve_mentions("$[alice:engineering].s1")

    # 新增 skill 后，不显式调用 scan()，第二次解析也必须 OK
    _write_fs_skill(root, name="s2")
    mgr.resolve_mentions("$[alice:engineering].s2")


def test_resolve_mentions_manual_does_not_auto_refresh_until_refresh_called(tmp_path: Path) -> None:
    """manual：未显式 refresh 时，新增 skill 不应被自动发现。"""

    root = tmp_path / "skills_root"
    _write_fs_skill(root, name="s1")
    mgr = _mk_fs_manager(tmp_path, root, scan={"refresh_policy": "manual", "ttl_sec": 300})

    mgr.resolve_mentions("$[alice:engineering].s1")

    _write_fs_skill(root, name="s2")
    with pytest.raises(FrameworkError) as exc_info:
        mgr.resolve_mentions("$[alice:engineering].s2")
    assert exc_info.value.code == "SKILL_UNKNOWN"

    mgr.refresh()
    mgr.resolve_mentions("$[alice:engineering].s2")
