"""
离线示例：redis/pgsql skills sources 的 scan（metadata-only）+ inject（lazy body loader）。

用途：
- 演示 `SkillsManager(..., source_clients=...)` 注入 fake redis/pgsql client 的方式；
- 明确并验证“scan 只读 metadata；inject 才读 body”的契约；
- 作为 `test_examples_smoke.py` 的离线回归示例脚本。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

from skills_runtime.skills.manager import SkillsManager


TS = "2026-02-07T00:00:00Z"


class FakeRedisClient:
    """最小 fake redis client：scan_iter/hgetall/get + 调用记录。"""

    def __init__(self, *, scan_keys: Iterable[Any], hashes: Mapping[str, Mapping[str, Any]], bodies: Mapping[str, Any]) -> None:
        self._scan_keys = list(scan_keys)
        self._hashes = {str(k): dict(v) for k, v in dict(hashes).items()}
        self._bodies = dict(bodies)
        self.scan_calls: List[str] = []
        self.hgetall_calls: List[str] = []
        self.get_calls: List[str] = []

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
        return self._bodies.get(key)


@dataclass
class _PgSkillRow:
    """内存表：用于 FakePgClient 的最小行模型。"""

    id: str
    namespace: str
    skill_name: str
    description: str
    created_at: str
    updated_at: str
    body: str
    enabled: bool = True
    body_size: Optional[int] = None
    body_etag: Optional[str] = None
    required_env_vars: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    scope: Optional[str] = None

    def to_scan_row(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "namespace": self.namespace,
            "skill_name": self.skill_name,
            "description": self.description,
            "body_size": self.body_size,
            "body_etag": self.body_etag,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "required_env_vars": self.required_env_vars,
            "metadata": self.metadata,
            "scope": self.scope,
        }


class FakePgCursor:
    """最小 fake pgsql cursor：execute/fetchall/fetchone + 调用记录。"""

    def __init__(self, *, table: List[_PgSkillRow], executed: List[Tuple[str, Any]]) -> None:
        self._table = list(table)
        self._executed = executed
        self._rows: List[Dict[str, Any]] = []
        self._one: Any = None

    def __enter__(self) -> "FakePgCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def execute(self, sql: str, params: Any) -> None:
        self._executed.append((sql, params))
        sql_norm = " ".join(str(sql).split()).lower()

        if sql_norm.startswith("select id, namespace, skill_name"):
            namespace = params[0] if isinstance(params, (tuple, list)) and params else None
            out = [r.to_scan_row() for r in self._table if r.enabled and r.namespace == namespace]
            self._rows = out
            self._one = None
            return

        if sql_norm.startswith("select body from"):
            row_id = params[0] if isinstance(params, (tuple, list)) and len(params) >= 2 else None
            namespace = params[1] if isinstance(params, (tuple, list)) and len(params) >= 2 else None
            rec = next((r for r in self._table if r.id == row_id and r.namespace == namespace), None)
            self._rows = []
            self._one = {"body": rec.body} if rec is not None else None
            return

        raise RuntimeError(f"unexpected SQL in fake pg cursor: {sql}")

    def fetchall(self) -> List[Any]:
        return list(self._rows)

    def fetchone(self) -> Any:
        return self._one


class FakePgClient:
    """最小 fake pgsql client：cursor() 返回可解释 SQL 的 cursor。"""

    def __init__(self, *, table: List[_PgSkillRow]) -> None:
        self._table = list(table)
        self.executed: List[Tuple[str, Any]] = []

    def cursor(self) -> FakePgCursor:
        return FakePgCursor(table=self._table, executed=self.executed)


def main() -> int:
    """脚本入口：构造 fake clients → scan → inject → 输出关键断言。"""

    parser = argparse.ArgumentParser(description="02_offline_sources_redis_and_pgsql")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    namespace = "alice:engineering"
    redis_skill_name = "python-testing-redis"
    pg_skill_name = "python-testing-pg"

    # --- Fake Redis fixtures (metadata-only scan + lazy body) ---
    key_prefix = "skills:"
    meta_key_redis = f"{key_prefix}meta:{namespace}:{redis_skill_name}"
    body_key_redis = f"{key_prefix}body:{namespace}:{redis_skill_name}"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key_redis],
        hashes={
            meta_key_redis: {
                "skill_name": redis_skill_name,
                "description": "redis source demo",
                "created_at": TS,
                "required_env_vars": "[]",
                "metadata": json.dumps({"tag": "offline-demo"}, ensure_ascii=False),
                "body_key": body_key_redis,
                "body_size": "12",
            }
        },
        bodies={
            body_key_redis: "# Redis Body\n",
        },
    )

    # --- Fake PgSQL fixtures (scan row + body backfill) ---
    pg_table: List[_PgSkillRow] = [
        _PgSkillRow(
            id="row-1",
            namespace=namespace,
            skill_name=pg_skill_name,
            description="pgsql source demo",
            created_at=TS,
            updated_at=TS,
            body="# PgSQL Body\n",
            body_size=11,
            body_etag="etag-1",
            required_env_vars=[],
            metadata={"tag": "offline-demo"},
            scope="repo",
            enabled=True,
        )
    ]
    pg_client = FakePgClient(table=pg_table)

    skills_config: Dict[str, Any] = {
        "spaces": [{"id": "space-eng", "namespace": namespace, "sources": ["src-redis", "src-pg"], "enabled": True}],
        "sources": [
            {"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": key_prefix}},
            {"id": "src-pg", "type": "pgsql", "options": {"dsn_env": "PG_DSN", "schema": "public", "table": "skills"}},
        ],
        # 避免示例外部依赖：只要 scan + inject；不开启 actions/ref-read/bundles。
        "scan": {"refresh_policy": "manual", "ttl_sec": 300},
    }

    mgr = SkillsManager(
        workspace_root=workspace_root,
        skills_config=skills_config,
        source_clients={"src-redis": redis_client, "src-pg": pg_client},
    )

    report = mgr.scan()
    print("[example] scan.stats:", json.dumps(report.stats, ensure_ascii=False))
    assert report.errors == [], [e.model_dump() for e in report.errors]
    assert report.stats.get("skills_total") == 2

    # scan must be metadata-only: no redis GET, no pg body query
    assert redis_client.get_calls == [], f"scan() should not read redis bodies: {redis_client.get_calls}"
    assert not any("select body from" in " ".join(sql.split()).lower() for sql, _p in pg_client.executed), pg_client.executed

    # inject should read body (lazy)
    skill_redis, mention_redis = mgr.resolve_mentions(f"$[{namespace}].{redis_skill_name}")[0]
    injected_redis = mgr.render_injected_skill(skill_redis, source="example", mention_text=mention_redis.mention_text)
    assert "Redis Body" in injected_redis
    assert redis_client.get_calls == [body_key_redis]

    skill_pg, mention_pg = mgr.resolve_mentions(f"$[{namespace}].{pg_skill_name}")[0]
    before_pg = list(pg_client.executed)
    injected_pg = mgr.render_injected_skill(skill_pg, source="example", mention_text=mention_pg.mention_text)
    assert "PgSQL Body" in injected_pg
    after_pg = list(pg_client.executed)
    assert len(after_pg) > len(before_pg), "inject should trigger an extra pgsql query to load body"

    print("EXAMPLE_OK: skills_sources_redis_pgsql_offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

