from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from skills_runtime.core.errors import FrameworkError
from skills_runtime.skills.manager import SkillsManager


pytestmark = pytest.mark.integration


run_integration = os.environ.get("SKILLS_RUNTIME_SDK_RUN_INTEGRATION")
if run_integration != "1":
    pytest.skip(
        "integration tests disabled (set SKILLS_RUNTIME_SDK_RUN_INTEGRATION=1)",
        allow_module_level=True,
    )


redis = pytest.importorskip("redis")
psycopg = pytest.importorskip("psycopg")

TS = "2026-02-07T00:00:00Z"


def _wait_until(fn, *, timeout_sec: float = 10.0, interval_sec: float = 0.2) -> None:
    """轮询等待某个条件成立；超时后 raise AssertionError。"""

    deadline = time.monotonic() + timeout_sec
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if fn():
                return
        except Exception as exc:  # pragma: no cover (仅用于记录最后一次异常)
            last_exc = exc
        time.sleep(interval_sec)
    if last_exc is not None:
        raise AssertionError(f"service not ready: {last_exc}") from last_exc
    raise AssertionError("service not ready")


@pytest.fixture()
def _integration_space_config() -> dict:
    """生成一份最小 skills_config（一个 space + 两个 sources）。"""

    return {
        "mode": "explicit",
        "spaces": [
            {
                "id": "space-acme-core",
                "account": "acme",
                "domain": "core",
                "sources": ["src-redis", "src-pg"],
                "enabled": True,
            }
        ],
        "sources": [
            {
                "id": "src-redis",
                "type": "redis",
                "options": {"dsn_env": "REDIS_URL", "key_prefix": "skills:"},
            },
            {
                "id": "src-pg",
                "type": "pgsql",
                "options": {"dsn_env": "SKILLS_PG_DSN", "schema": "agent", "table": "skills_catalog"},
            },
        ],
        "injection": {"max_bytes": 64 * 1024},
    }


@pytest.fixture()
def redis_client() -> object:
    """返回 redis client（真实连接，来自 env DSN）。"""

    url = os.environ["REDIS_URL"]
    client = redis.from_url(url)

    def _ready() -> bool:
        return client.ping() is True

    _wait_until(_ready)
    return client


@pytest.fixture()
def pg_conn() -> object:
    """返回 psycopg connection（真实连接，来自 env DSN）。"""

    dsn = os.environ["SKILLS_PG_DSN"]

    def _ready() -> bool:
        # 注意：服务尚未 ready 时，connect 可能阻塞较久；这里依赖 DSN 中的 connect_timeout。
        conn_probe = psycopg.connect(dsn)
        try:
            with conn_probe.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        finally:
            conn_probe.close()

    _wait_until(_ready)
    conn = psycopg.connect(dsn)
    return conn


@pytest.fixture()
def seeded_sources(redis_client: object, pg_conn: object) -> dict[str, str]:
    """在 Redis/Postgres 中写入最小数据集，并返回用于断言的 skill_name。"""

    account = "acme"
    domain = "core"
    prefix = "skills:"

    # Redis：一个正常 skill + 一个缺 body 的 skill（用于验证 scan metadata-only）
    redis_skill_ok = "redis_ok"
    redis_skill_missing_body = "redis_missing_body"

    # 清理可能存在的历史 key
    for name in (redis_skill_ok, redis_skill_missing_body):
        meta_key = f"{prefix}meta:{account}:{domain}:{name}"
        body_key = f"{prefix}body:{account}:{domain}:{name}"
        redis_client.delete(meta_key)  # type: ignore[attr-defined]
        redis_client.delete(body_key)  # type: ignore[attr-defined]

    meta_ok = {
        "skill_name": redis_skill_ok,
        "description": "redis integration ok",
        "created_at": TS,
        "required_env_vars": json.dumps([]),
        "metadata": json.dumps({"kind": "integration"}),
    }
    redis_client.hset(f"{prefix}meta:{account}:{domain}:{redis_skill_ok}", mapping=meta_ok)  # type: ignore[attr-defined]
    redis_client.set(  # type: ignore[attr-defined]
        f"{prefix}body:{account}:{domain}:{redis_skill_ok}",
        "# SKILL\n\nThis body is stored in redis.\n",
    )

    meta_missing = {
        "skill_name": redis_skill_missing_body,
        "description": "redis integration missing body",
        "created_at": TS,
        "required_env_vars": json.dumps([]),
        "metadata": json.dumps({"kind": "integration"}),
    }
    redis_client.hset(  # type: ignore[attr-defined]
        f"{prefix}meta:{account}:{domain}:{redis_skill_missing_body}",
        mapping=meta_missing,
    )
    # 注意：故意不写 body key

    # PgSQL：写入两条记录（enabled true/false）
    # 注意：schema/table 由 docker initdb migration 创建；集成测试不在此处执行 DDL。
    schema = "agent"
    table = "skills_catalog"
    pg_skill_ok = "pg_ok"
    pg_skill_disabled = "pg_disabled"
    pg_metadata_json = json.dumps({"kind": "integration"})

    with pg_conn.cursor() as cur:  # type: ignore[attr-defined]
        # 幂等清理：删除本次测试用的两条 name
        cur.execute(
            f'DELETE FROM "{schema}"."{table}" WHERE account=%s AND domain=%s AND skill_name IN (%s, %s)',
            (account, domain, pg_skill_ok, pg_skill_disabled),
        )
        cur.execute(
            f"""
            INSERT INTO "{schema}"."{table}"
              (account, domain, skill_name, description, body, enabled, body_size, created_at, metadata, scope)
            VALUES
              (%s,%s,%s,%s,%s,TRUE,%s,%s,%s::jsonb,%s),
              (%s,%s,%s,%s,%s,FALSE,%s,%s,%s::jsonb,%s)
            """,
            (
                account,
                domain,
                pg_skill_ok,
                "pgsql integration ok",
                "# SKILL\n\nThis body is stored in pgsql.\n",
                len("# SKILL\n\nThis body is stored in pgsql.\n".encode("utf-8")),
                TS,
                pg_metadata_json,
                "pgsql",
                account,
                domain,
                pg_skill_disabled,
                "pgsql disabled",
                "# SKILL\n\nThis body is disabled.\n",
                len("# SKILL\n\nThis body is disabled.\n".encode("utf-8")),
                TS,
                pg_metadata_json,
                "pgsql",
            ),
        )
    pg_conn.commit()  # type: ignore[attr-defined]

    return {
        "redis_ok": redis_skill_ok,
        "redis_missing_body": redis_skill_missing_body,
        "pg_ok": pg_skill_ok,
        "pg_disabled": pg_skill_disabled,
    }


def test_integration_scan_is_metadata_only(_integration_space_config: dict, seeded_sources: dict[str, str], tmp_path: Path) -> None:
    mgr = SkillsManager(workspace_root=tmp_path, skills_config=_integration_space_config)
    report = mgr.scan()

    assert not report.errors
    skill_names = [s.skill_name for s in report.skills]
    assert seeded_sources["redis_ok"] in skill_names
    assert seeded_sources["redis_missing_body"] in skill_names
    assert seeded_sources["pg_ok"] in skill_names
    # enabled=false 不应出现在 scan 结果
    assert seeded_sources["pg_disabled"] not in skill_names

    by_name = {s.skill_name: s for s in report.skills}
    assert by_name[seeded_sources["redis_ok"]].metadata.get("created_at") == TS
    assert by_name[seeded_sources["redis_missing_body"]].metadata.get("created_at") == TS
    assert by_name[seeded_sources["pg_ok"]].metadata.get("created_at") == TS


def test_integration_render_injected_skill_reads_body_on_demand(
    _integration_space_config: dict, seeded_sources: dict[str, str], tmp_path: Path
) -> None:
    mgr = SkillsManager(workspace_root=tmp_path, skills_config=_integration_space_config)
    mgr.scan()

    # Redis ok：注入时读取正文
    [(redis_skill, _)] = mgr.resolve_mentions(f"$[acme:core].{seeded_sources['redis_ok']}")
    rendered = mgr.render_injected_skill(redis_skill, source="mention", mention_text=None)
    assert "This body is stored in redis." in rendered

    # PgSQL ok：注入时读取正文
    [(pg_skill, _)] = mgr.resolve_mentions(f"$[acme:core].{seeded_sources['pg_ok']}")
    rendered_pg = mgr.render_injected_skill(pg_skill, source="mention", mention_text=None)
    assert "This body is stored in pgsql." in rendered_pg


def test_integration_redis_missing_body_fails_on_inject(
    _integration_space_config: dict, seeded_sources: dict[str, str], tmp_path: Path
) -> None:
    mgr = SkillsManager(workspace_root=tmp_path, skills_config=_integration_space_config)
    mgr.scan()

    [(skill, _)] = mgr.resolve_mentions(f"$[acme:core].{seeded_sources['redis_missing_body']}")
    with pytest.raises(FrameworkError) as ei:
        _ = mgr.render_injected_skill(skill, source="mention", mention_text=None)
    assert ei.value.code == "SKILL_BODY_READ_FAILED"


def test_integration_unknown_mention_is_error(_integration_space_config: dict, seeded_sources: dict[str, str], tmp_path: Path) -> None:
    mgr = SkillsManager(workspace_root=tmp_path, skills_config=_integration_space_config)
    mgr.scan()

    with pytest.raises(FrameworkError) as ei:
        _ = mgr.resolve_mentions("$[acme:core].does_not_exist")
    assert ei.value.code == "SKILL_UNKNOWN"
