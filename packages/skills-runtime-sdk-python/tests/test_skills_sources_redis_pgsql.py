from __future__ import annotations

import builtins
from dataclasses import dataclass
from pathlib import Path
import re
import sys
import types
from typing import Any, Dict, Iterable, List, Mapping, Optional

import pytest

from skills_runtime.config.loader import AgentSdkSkillsConfig, load_config_dicts
from skills_runtime.core.errors import FrameworkError
from skills_runtime.skills.manager import SkillsManager


TS = "2026-02-07T00:00:00Z"


def _base_config(skills: dict[str, Any]) -> dict[str, Any]:
    """构造最小可校验配置。"""

    return {
        "config_version": 1,
        "run": {"max_steps": 5},
        "llm": {"base_url": "http://example.test/v1", "api_key_env": "OPENAI_API_KEY"},
        "models": {"planner": "planner-x", "executor": "executor-x"},
        "skills": skills,
    }


def _build_skills_config(skills: dict[str, Any]) -> AgentSdkSkillsConfig:
    """从 dict 构造 `AgentSdkSkillsConfig`。"""

    return load_config_dicts([_base_config(skills)]).skills


def _mk_manager(
    tmp_path: Path,
    *,
    skills: dict[str, Any],
    in_memory_registry: Optional[dict[str, list[dict[str, Any]]]] = None,
    source_clients: Optional[dict[str, Any]] = None,
) -> SkillsManager:
    """创建 SkillsManager 测试实例。"""

    return SkillsManager(
        workspace_root=tmp_path,
        skills_config=_build_skills_config(skills),
        in_memory_registry=in_memory_registry or {},
        source_clients=source_clients or {},
    )


def _write_fs_skill(root: Path, *, name: str, description: str) -> Path:
    """写入 filesystem skill fixture。"""

    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f'description: "{description}"',
                "---",
                "# Body",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return skill_md


class FakeRedisClient:
    """最小 Redis fake client（覆盖 scan_iter/hgetall/get）。"""

    def __init__(
        self,
        *,
        scan_keys: Optional[Iterable[Any]] = None,
        hashes: Optional[Dict[str, Any]] = None,
        bodies: Optional[Dict[str, Any]] = None,
        scan_error: Optional[Exception] = None,
        hgetall_errors: Optional[Dict[str, Exception]] = None,
    ) -> None:
        """初始化 fake redis 行为。"""

        self._scan_keys = list(scan_keys or [])
        self._hashes = dict(hashes or {})
        self._bodies = dict(bodies or {})
        self._scan_error = scan_error
        self._hgetall_errors = dict(hgetall_errors or {})
        self.scan_calls: List[str] = []
        self.hgetall_calls: List[str] = []
        self.get_calls: List[str] = []

    def _key(self, raw: Any) -> str:
        """归一化 key 为字符串。"""

        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    def scan_iter(self, *, match: str):
        """模拟 scan_iter。"""

        self.scan_calls.append(match)
        if self._scan_error is not None:
            raise self._scan_error
        for key in self._scan_keys:
            yield key

    def hgetall(self, raw_key: Any) -> Any:
        """模拟 hgetall。"""

        key = self._key(raw_key)
        self.hgetall_calls.append(key)
        err = self._hgetall_errors.get(key)
        if err is not None:
            raise err
        return self._hashes.get(key, {})

    def get(self, raw_key: Any) -> Any:
        """模拟 get。"""

        key = self._key(raw_key)
        self.get_calls.append(key)
        return self._bodies.get(key)


@dataclass
class FakePgCursor:
    """最小 pgsql cursor fake。"""

    rows: Optional[List[Any]] = None
    one: Any = None
    description: Any = None
    execute_error: Optional[Exception] = None

    def __post_init__(self) -> None:
        """初始化运行时状态。"""

        self.executed: List[tuple[str, Any]] = []

    def __enter__(self) -> "FakePgCursor":
        """进入上下文。"""

        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        """退出上下文。"""

        return False

    def execute(self, sql: str, params: Any) -> None:
        """记录并执行 SQL。"""

        self.executed.append((sql, params))
        if self.execute_error is not None:
            raise self.execute_error

    def fetchall(self) -> List[Any]:
        """返回全部行。"""

        return list(self.rows or [])

    def fetchone(self) -> Any:
        """返回单行。"""

        return self.one


class FakePgClient:
    """最小 pgsql client fake（按顺序返回 cursor）。"""

    def __init__(self, cursors: List[FakePgCursor]) -> None:
        """初始化 cursor 队列。"""

        self._cursors = list(cursors)
        self.cursor_calls = 0

    def cursor(self) -> FakePgCursor:
        """返回下一个 cursor。"""

        self.cursor_calls += 1
        if not self._cursors:
            raise RuntimeError("no fake cursor left")
        return self._cursors.pop(0)


class FakePgClientClosable(FakePgClient):
    """带 close() 的 pgsql fake client（用于验证 factory/pool 的释放语义）。"""

    def __init__(self, cursors: List[FakePgCursor], *, closed: List[str], tag: str) -> None:
        super().__init__(cursors)
        self._closed = closed
        self._tag = tag

    def close(self) -> None:
        self._closed.append(self._tag)


def _redis_skills_config(source_options: dict[str, Any], *, include_fs: bool = False, include_mem: bool = False) -> dict[str, Any]:
    """构造 redis 场景 skills 配置。"""

    sources: list[dict[str, Any]] = [{"id": "src-redis", "type": "redis", "options": source_options}]
    space_sources = ["src-redis"]
    if include_fs:
        sources.append({"id": "src-fs", "type": "filesystem", "options": {"root": "./skills"}})
        space_sources.append("src-fs")
    if include_mem:
        sources.append({"id": "src-mem", "type": "in-memory", "options": {"namespace": "ns-redis"}})
        space_sources.append("src-mem")
    return {
        "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": space_sources}],
        "sources": sources,
        # 避免 resolve_mentions 触发额外 scan 影响“source 行为”断言（refresh_policy 的语义在专门用例中覆盖）。
        "scan": {"refresh_policy": "manual", "ttl_sec": 300},
    }


def _pgsql_skills_config(source_options: dict[str, Any], *, include_fs: bool = False, include_mem: bool = False) -> dict[str, Any]:
    """构造 pgsql 场景 skills 配置。"""

    sources: list[dict[str, Any]] = [{"id": "src-pg", "type": "pgsql", "options": source_options}]
    space_sources = ["src-pg"]
    if include_fs:
        sources.append({"id": "src-fs", "type": "filesystem", "options": {"root": "./skills"}})
        space_sources.append("src-fs")
    if include_mem:
        sources.append({"id": "src-mem", "type": "in-memory", "options": {"namespace": "ns-pg"}})
        space_sources.append("src-mem")
    return {
        "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": space_sources}],
        "sources": sources,
        # 避免 resolve_mentions 触发额外 scan 影响“source 行为”断言（refresh_policy 的语义在专门用例中覆盖）。
        "scan": {"refresh_policy": "manual", "ttl_sec": 300},
    }


def _assert_has_error(report, code: str) -> dict[str, Any]:
    """断言报告包含指定错误码并返回 details。"""

    for item in report.errors:
        if item.code == code:
            return dict(item.details)
    raise AssertionError(f"missing error code: {code}")


def test_redis_source_scan_and_inject_success(tmp_path: Path) -> None:
    """Redis: scan 只读 metadata，inject 才读 body。"""

    meta_key = "skills:meta:alice:engineering:python_testing"
    body_key = "skills:body:alice:engineering:python_testing"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key.encode("utf-8")],
        hashes={
            meta_key: {
                b"skill_name": b"python_testing",
                b"description": b"pytest patterns",
                b"created_at": TS.encode("utf-8"),
                b"body_size": b"12",
                b"required_env_vars": b'["OPENAI_API_KEY"]',
                b"metadata": b'{"tag":"qa"}',
                b"body_key": body_key.encode("utf-8"),
                b"etag": b"etag-1",
                b"updated_at": TS.encode("utf-8"),
                b"scope": b"repo",
            }
        },
        bodies={body_key: "# Redis Body\n"},
    )

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    report = mgr.scan()
    assert report.stats["skills_total"] == 1
    assert report.errors == []
    assert redis_client.get_calls == []

    skill, mention = mgr.resolve_mentions("$[alice:engineering].python_testing")[0]
    assert skill.metadata.get("created_at") == TS
    rendered = mgr.render_injected_skill(skill, source="mention", mention_text=mention.mention_text)
    assert "<name>python_testing</name>" in rendered
    assert redis_client.get_calls == [body_key]


def test_redis_source_default_body_key_success(tmp_path: Path) -> None:
    """Redis: body_key 缺失时使用默认 key 规则。"""

    meta_key = "skills:meta:alice:engineering:python_testing"
    default_body_key = "skills:body:alice:engineering:python_testing"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={
            meta_key: {
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "created_at": TS,
                "required_env_vars": "[]",
                "metadata": "{}",
            }
        },
        bodies={default_body_key: "# Default Body\n"},
    )

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )
    mgr.scan()
    skill, mention = mgr.resolve_mentions("$[alice:engineering].python_testing")[0]
    rendered = mgr.render_injected_skill(skill, source="mention", mention_text=mention.mention_text)
    assert "Default Body" in rendered
    assert redis_client.get_calls == [default_body_key]


def test_redis_source_missing_created_at_issue(tmp_path: Path) -> None:
    """Redis: created_at 缺失时返回 metadata invalid（框架层严格）。"""

    meta_key = "skills:meta:alice:engineering:python_testing"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={
            meta_key: {
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "required_env_vars": "[]",
                "metadata": "{}",
            }
        },
    )

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "created_at"


def test_redis_source_scan_iter_failed_issue(tmp_path: Path) -> None:
    """Redis: scan_iter 抛错时返回 source unavailable。"""

    redis_client = FakeRedisClient(scan_error=RuntimeError("scan boom"))
    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert "redis scan failed" in str(details.get("reason"))


def test_redis_source_hgetall_failed_issue(tmp_path: Path) -> None:
    """Redis: hgetall 抛错时记录 source unavailable。"""

    meta_key = "skills:meta:alice:engineering:python_testing"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hgetall_errors={meta_key: RuntimeError("hgetall boom")},
    )
    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert details.get("locator") == f"redis://{meta_key}"


def test_redis_source_hgetall_non_mapping_issue(tmp_path: Path) -> None:
    """Redis: hgetall 返回非 mapping 时返回 metadata invalid。"""

    meta_key = "skills:meta:alice:engineering:python_testing"
    redis_client = FakeRedisClient(scan_keys=[meta_key], hashes={meta_key: ["not", "mapping"]})
    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("locator") == f"redis://{meta_key}"


def test_redis_source_body_missing_failed(tmp_path: Path) -> None:
    """Redis: body 缺失时注入阶段报 `SKILL_BODY_READ_FAILED`。"""

    meta_key = "skills:meta:alice:engineering:python_testing"
    body_key = "skills:body:alice:engineering:python_testing"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={
            meta_key: {
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "created_at": TS,
                "required_env_vars": "[]",
                "metadata": "{}",
                "body_key": body_key,
            }
        },
    )
    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    mgr.scan()
    skill, mention = mgr.resolve_mentions("$[alice:engineering].python_testing")[0]
    with pytest.raises(FrameworkError) as exc_info:
        mgr.render_injected_skill(skill, source="mention", mention_text=mention.mention_text)
    assert exc_info.value.code == "SKILL_BODY_READ_FAILED"


def test_redis_source_missing_dsn_env_issue(tmp_path: Path) -> None:
    """Redis: 未注入 client 时缺失 dsn_env 报 metadata invalid。"""

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "dsn_env"


def test_redis_source_missing_key_prefix_issue(tmp_path: Path) -> None:
    """Redis: 缺失 key_prefix 报 metadata invalid。"""

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"dsn_env": "REDIS_URL"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "key_prefix"


def test_redis_source_invalid_metadata_json_issue(tmp_path: Path) -> None:
    """Redis: metadata JSON 非法时报 metadata invalid。"""

    meta_key = "skills:meta:alice:engineering:python_testing"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={
            meta_key: {
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "created_at": TS,
                "required_env_vars": "[]",
                "metadata": "{not-json}",
            }
        },
    )
    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "metadata"


def test_redis_source_invalid_required_env_vars_issue(tmp_path: Path) -> None:
    """Redis: required_env_vars 非 list[str] 时报 metadata invalid。"""

    meta_key = "skills:meta:alice:engineering:python_testing"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={
            meta_key: {
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "created_at": TS,
                "required_env_vars": "[1,2]",
                "metadata": "{}",
            }
        },
    )
    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "required_env_vars"


def test_redis_source_duplicate_with_filesystem(tmp_path: Path) -> None:
    """Redis: 与 filesystem 同名 skill 必须在 scan 早失败。"""

    fs_root = tmp_path / "skills"
    _write_fs_skill(fs_root, name="dup_name", description="from fs")

    meta_key = "skills:meta:alice:engineering:dup_name"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={
            meta_key: {
                "skill_name": "dup_name",
                "description": "from redis",
                "created_at": TS,
                "required_env_vars": "[]",
                "metadata": "{}",
            }
        },
    )
    skills = _redis_skills_config({"key_prefix": "skills:"}, include_fs=True)
    skills["sources"] = [
        {"id": "src-redis", "type": "redis", "options": {"key_prefix": "skills:"}},
        {"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}},
    ]

    mgr = _mk_manager(tmp_path, skills=skills, source_clients={"src-redis": redis_client})
    with pytest.raises(FrameworkError) as exc_info:
        mgr.scan()
    assert exc_info.value.code == "SKILL_DUPLICATE_NAME"


def test_redis_source_duplicate_with_in_memory(tmp_path: Path) -> None:
    """Redis: 与 in-memory 同名 skill 必须在 scan 早失败。"""

    meta_key = "skills:meta:alice:engineering:dup_name"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={
            meta_key: {
                "skill_name": "dup_name",
                "description": "from redis",
                "created_at": TS,
                "required_env_vars": "[]",
                "metadata": "{}",
            }
        },
    )
    skills = _redis_skills_config({"key_prefix": "skills:"}, include_mem=True)
    in_memory_registry = {
        "ns-redis": [
            {
                "skill_name": "dup_name",
                "description": "from mem",
                "body": "# mem\n",
            }
        ]
    }

    mgr = _mk_manager(
        tmp_path,
        skills=skills,
        in_memory_registry=in_memory_registry,
        source_clients={"src-redis": redis_client},
    )
    with pytest.raises(FrameworkError) as exc_info:
        mgr.scan()
    assert exc_info.value.code == "SKILL_DUPLICATE_NAME"


def test_pgsql_source_scan_and_inject_success_dict_rows(tmp_path: Path) -> None:
    """PgSQL: dict rows 成功扫描并在注入时读取 body。"""

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "body_size": 11,
                "body_etag": "etag-1",
                "created_at": TS,
                "updated_at": TS,
                "required_env_vars": ["OPENAI_API_KEY"],
                "metadata": {"tag": "qa"},
                "scope": "repo",
            }
        ]
    )
    body_cursor = FakePgCursor(one={"body": "# PgSQL Body\n"})
    pg_client = FakePgClient([meta_cursor, body_cursor])

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    report = mgr.scan()
    assert report.stats["skills_total"] == 1
    assert report.errors == []
    assert pg_client.cursor_calls == 1
    [(scan_sql, _params)] = meta_cursor.executed
    assert re.search(r"\bbody\b", scan_sql, flags=re.IGNORECASE) is None

    skill, mention = mgr.resolve_mentions("$[alice:engineering].python_testing")[0]
    rendered = mgr.render_injected_skill(skill, source="mention", mention_text=mention.mention_text)
    assert "PgSQL Body" in rendered
    assert pg_client.cursor_calls == 2
    [(body_sql, _body_params)] = body_cursor.executed
    assert re.search(r"\bbody\b", body_sql, flags=re.IGNORECASE) is not None


def test_pgsql_source_factory_is_used_and_released_for_body_load(tmp_path: Path) -> None:
    """PgSQL: 注入 factory 时，scan/body_loader 均应获取新 client 并在使用后释放。"""

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "body_size": 11,
                "body_etag": "etag-1",
                "created_at": TS,
                "updated_at": TS,
                "required_env_vars": [],
                "metadata": {},
                "scope": "repo",
            }
        ]
    )
    body_cursor = FakePgCursor(one={"body": "# Body\n"})

    closed: List[str] = []
    calls = {"n": 0}

    def _factory() -> FakePgClientClosable:
        calls["n"] += 1
        if calls["n"] == 1:
            return FakePgClientClosable([meta_cursor], closed=closed, tag="scan")
        return FakePgClientClosable([body_cursor], closed=closed, tag="body")

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": _factory},
    )

    report = mgr.scan()
    assert report.errors == []
    assert calls["n"] == 1
    assert closed == ["scan"]

    skill, mention = mgr.resolve_mentions("$[alice:engineering].python_testing")[0]
    rendered = mgr.render_injected_skill(skill, source="mention", mention_text=mention.mention_text)
    assert "Body" in rendered
    assert calls["n"] == 2
    assert closed == ["scan", "body"]


def test_pgsql_source_scan_and_inject_success_tuple_rows(tmp_path: Path) -> None:
    """PgSQL: tuple rows + description 映射成功。"""

    columns = [
        ("id",),
        ("account",),
        ("domain",),
        ("skill_name",),
        ("description",),
        ("body_size",),
        ("body_etag",),
        ("created_at",),
        ("updated_at",),
        ("required_env_vars",),
        ("metadata",),
        ("scope",),
    ]
    meta_cursor = FakePgCursor(
        rows=[
            (
                2,
                "alice",
                "engineering",
                "python_testing",
                "pytest patterns",
                9,
                "etag-2",
                TS,
                TS,
                ["OPENAI_API_KEY"],
                {"tag": "tuple"},
                "repo",
            )
        ],
        description=columns,
    )
    body_cursor = FakePgCursor(one=("# tuple body\n",))
    pg_client = FakePgClient([meta_cursor, body_cursor])

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    mgr.scan()
    skill, mention = mgr.resolve_mentions("$[alice:engineering].python_testing")[0]
    rendered = mgr.render_injected_skill(skill, source="mention", mention_text=mention.mention_text)
    assert "tuple body" in rendered


def test_pgsql_source_missing_created_at_issue(tmp_path: Path) -> None:
    """PgSQL: created_at 缺失时返回 metadata invalid（框架层严格）。"""

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "body_size": 11,
                "body_etag": "etag-1",
                "updated_at": TS,
                "required_env_vars": [],
                "metadata": {},
                "scope": "repo",
            }
        ]
    )
    pg_client = FakePgClient([meta_cursor])

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "created_at"


def test_pgsql_source_missing_schema_issue(tmp_path: Path) -> None:
    """PgSQL: 缺失 schema 报 metadata invalid。"""

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"table": "skills"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "schema"


def test_pgsql_source_invalid_table_issue(tmp_path: Path) -> None:
    """PgSQL: 非法 table 标识符报 metadata invalid。"""

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills;drop"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "table"


def test_pgsql_source_missing_dsn_env_issue(tmp_path: Path) -> None:
    """PgSQL: 未注入 client 时缺失 dsn_env 报 metadata invalid。"""

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "dsn_env"


def test_pgsql_source_query_failed_issue(tmp_path: Path) -> None:
    """PgSQL: query 抛错时返回 source unavailable。"""

    pg_client = FakePgClient([FakePgCursor(execute_error=RuntimeError("db down"))])
    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert "pgsql query failed" in str(details.get("reason"))


def test_pgsql_source_missing_cursor_description_issue(tmp_path: Path) -> None:
    """PgSQL: tuple rows 且 description 缺失时报 source unavailable。"""

    pg_client = FakePgClient(
        [
            FakePgCursor(
                rows=[(1, "alice", "engineering", "python_testing", "desc", 1, "etag", "ts", [], {}, "repo")],
                description=None,
            )
        ]
    )
    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert "cursor.description" in str(details.get("reason"))


def test_pgsql_source_body_missing_failed(tmp_path: Path) -> None:
    """PgSQL: body 缺失时注入报 `SKILL_BODY_READ_FAILED`。"""

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "body_size": 5,
                "body_etag": "etag-1",
                "created_at": TS,
                "updated_at": TS,
                "required_env_vars": [],
                "metadata": {},
                "scope": "repo",
            }
        ]
    )
    body_cursor = FakePgCursor(one=None)
    pg_client = FakePgClient([meta_cursor, body_cursor])

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    mgr.scan()
    skill, mention = mgr.resolve_mentions("$[alice:engineering].python_testing")[0]
    with pytest.raises(FrameworkError) as exc_info:
        mgr.render_injected_skill(skill, source="mention", mention_text=mention.mention_text)
    assert exc_info.value.code == "SKILL_BODY_READ_FAILED"


def test_pgsql_source_invalid_required_env_vars_issue(tmp_path: Path) -> None:
    """PgSQL: required_env_vars 非 list[str] 时报 metadata invalid。"""

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "body_size": 10,
                "body_etag": "etag",
                "created_at": TS,
                "updated_at": "ts",
                "required_env_vars": "OPENAI_API_KEY",
                "metadata": {},
                "scope": "repo",
            }
        ]
    )
    pg_client = FakePgClient([meta_cursor])

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "required_env_vars"


def test_pgsql_source_invalid_metadata_issue(tmp_path: Path) -> None:
    """PgSQL: metadata 非 dict 时报 metadata invalid。"""

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "body_size": 10,
                "body_etag": "etag",
                "created_at": TS,
                "updated_at": "ts",
                "required_env_vars": [],
                "metadata": "{}",
                "scope": "repo",
            }
        ]
    )
    pg_client = FakePgClient([meta_cursor])

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_METADATA_INVALID")
    assert details.get("field") == "metadata"


def test_pgsql_source_duplicate_with_filesystem(tmp_path: Path) -> None:
    """PgSQL: 与 filesystem 同名 skill 必须在 scan 早失败。"""

    fs_root = tmp_path / "skills"
    _write_fs_skill(fs_root, name="dup_name", description="from fs")

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "dup_name",
                "description": "from pg",
                "body_size": 10,
                "body_etag": "etag",
                "created_at": TS,
                "updated_at": "ts",
                "required_env_vars": [],
                "metadata": {},
                "scope": "repo",
            }
        ]
    )
    pg_client = FakePgClient([meta_cursor])

    skills = _pgsql_skills_config({"schema": "public", "table": "skills"}, include_fs=True)
    skills["sources"] = [
        {"id": "src-pg", "type": "pgsql", "options": {"schema": "public", "table": "skills"}},
        {"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}},
    ]

    mgr = _mk_manager(tmp_path, skills=skills, source_clients={"src-pg": pg_client})
    with pytest.raises(FrameworkError) as exc_info:
        mgr.scan()
    assert exc_info.value.code == "SKILL_DUPLICATE_NAME"


def test_pgsql_source_duplicate_with_in_memory(tmp_path: Path) -> None:
    """PgSQL: 与 in-memory 同名 skill 必须在 scan 早失败。"""

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "dup_name",
                "description": "from pg",
                "body_size": 10,
                "body_etag": "etag",
                "created_at": TS,
                "updated_at": "ts",
                "required_env_vars": [],
                "metadata": {},
                "scope": "repo",
            }
        ]
    )
    pg_client = FakePgClient([meta_cursor])

    skills = _pgsql_skills_config({"schema": "public", "table": "skills"}, include_mem=True)
    in_memory_registry = {
        "ns-pg": [
            {
                "skill_name": "dup_name",
                "description": "from mem",
                "body": "# mem\n",
            }
        ]
    }

    mgr = _mk_manager(
        tmp_path,
        skills=skills,
        in_memory_registry=in_memory_registry,
        source_clients={"src-pg": pg_client},
    )
    with pytest.raises(FrameworkError) as exc_info:
        mgr.scan()
    assert exc_info.value.code == "SKILL_DUPLICATE_NAME"


def test_redis_source_dsn_env_present_but_env_missing_reports_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis: dsn_env 存在但环境变量缺失时，scan 收敛为 source unavailable（env_present=false）。"""

    monkeypatch.delenv("REDIS_URL", raising=False)

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"dsn_env": "REDIS_URL", "key_prefix": "skills:"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert details.get("source_type") == "redis"
    assert details.get("dsn_env") == "REDIS_URL"
    assert details.get("env_present") is False


def test_redis_source_dependency_missing_reports_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis: redis 依赖缺失时，scan 收敛为 source unavailable（reason 包含 dependency unavailable）。"""

    monkeypatch.setenv("REDIS_URL", "redis://example.test/0")

    real_import = builtins.__import__

    def fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if name == "redis":
            raise ModuleNotFoundError("No module named 'redis'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"dsn_env": "REDIS_URL", "key_prefix": "skills:"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert details.get("source_type") == "redis"
    assert details.get("dsn_env") == "REDIS_URL"
    assert details.get("env_present") is True
    assert "dependency unavailable" in str(details.get("reason"))


def test_redis_source_connect_failed_reports_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis: from_url 连接失败时，scan 收敛为 source unavailable（reason 包含 connect failed）。"""

    monkeypatch.setenv("REDIS_URL", "redis://example.test/0")

    fake_redis = types.ModuleType("redis")

    def from_url(_: str) -> Any:
        raise RuntimeError("dial failed")

    fake_redis.from_url = from_url  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", fake_redis)

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"dsn_env": "REDIS_URL", "key_prefix": "skills:"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert details.get("source_type") == "redis"
    assert details.get("env_present") is True
    assert "connect failed" in str(details.get("reason"))


def test_redis_source_injected_client_ignores_env_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis: 注入 client 时不读取 env（即使 dsn_env 存在且 env 缺失也可 scan 成功）。"""

    monkeypatch.delenv("REDIS_URL", raising=False)

    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("should not read dsn from env when client is injected")

    monkeypatch.setattr(SkillsManager, "_source_dsn_from_env", boom)

    meta_key = "skills:meta:alice:engineering:python_testing"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={
            meta_key: {
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "created_at": TS,
                "required_env_vars": "[]",
                "metadata": "{}",
            }
        },
    )

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"dsn_env": "REDIS_URL", "key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    report = mgr.scan()
    assert report.stats["skills_total"] == 1
    assert report.errors == []


def test_redis_source_injected_client_ignores_dependency_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis: 注入 client 时不 import redis（即使 import 被强制失败也可 scan 成功）。"""

    real_import = builtins.__import__

    def fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if name == "redis":
            raise ModuleNotFoundError("No module named 'redis'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("should not read dsn from env when client is injected")

    monkeypatch.setattr(SkillsManager, "_source_dsn_from_env", boom)

    meta_key = "skills:meta:alice:engineering:python_testing"
    redis_client = FakeRedisClient(
        scan_keys=[meta_key],
        hashes={
            meta_key: {
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "created_at": TS,
                "required_env_vars": "[]",
                "metadata": "{}",
            }
        },
    )

    mgr = _mk_manager(
        tmp_path,
        skills=_redis_skills_config({"key_prefix": "skills:"}),
        source_clients={"src-redis": redis_client},
    )

    report = mgr.scan()
    assert report.stats["skills_total"] == 1
    assert report.errors == []


def test_pgsql_source_dsn_env_present_but_env_missing_reports_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PgSQL: dsn_env 存在但环境变量缺失时，scan 收敛为 source unavailable（env_present=false）。"""

    monkeypatch.delenv("PG_DSN", raising=False)

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"dsn_env": "PG_DSN", "schema": "public", "table": "skills"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert details.get("source_type") == "pgsql"
    assert details.get("dsn_env") == "PG_DSN"
    assert details.get("env_present") is False


def test_pgsql_source_dependency_missing_reports_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PgSQL: psycopg 依赖缺失时，scan 收敛为 source unavailable（reason 包含 dependency unavailable）。"""

    monkeypatch.setenv("PG_DSN", "postgresql://example.test/db")

    real_import = builtins.__import__

    def fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if name == "psycopg":
            raise ModuleNotFoundError("No module named 'psycopg'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"dsn_env": "PG_DSN", "schema": "public", "table": "skills"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert details.get("source_type") == "pgsql"
    assert details.get("dsn_env") == "PG_DSN"
    assert details.get("env_present") is True
    assert "dependency unavailable" in str(details.get("reason"))


def test_pgsql_source_connect_failed_reports_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PgSQL: connect 失败时，scan 收敛为 source unavailable（reason 包含 connect failed）。"""

    monkeypatch.setenv("PG_DSN", "postgresql://example.test/db")

    fake_psycopg = types.ModuleType("psycopg")

    def connect(_: str) -> Any:
        raise RuntimeError("dial failed")

    fake_psycopg.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"dsn_env": "PG_DSN", "schema": "public", "table": "skills"}),
    )

    report = mgr.scan()
    details = _assert_has_error(report, "SKILL_SCAN_SOURCE_UNAVAILABLE")
    assert details.get("source_type") == "pgsql"
    assert details.get("env_present") is True
    assert "connect failed" in str(details.get("reason"))


def test_pgsql_source_injected_client_ignores_env_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PgSQL: 注入 client 时不读取 env（即使 dsn_env 存在且 env 缺失也可 scan 成功）。"""

    monkeypatch.delenv("PG_DSN", raising=False)

    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("should not read dsn from env when client is injected")

    monkeypatch.setattr(SkillsManager, "_source_dsn_from_env", boom)

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "body_size": 0,
                "body_etag": "etag",
                "created_at": TS,
                "updated_at": "ts",
                "required_env_vars": [],
                "metadata": {},
                "scope": "repo",
            }
        ]
    )
    pg_client = FakePgClient([meta_cursor])

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"dsn_env": "PG_DSN", "schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    report = mgr.scan()
    assert report.stats["skills_total"] == 1
    assert report.errors == []


def test_pgsql_source_injected_client_ignores_dependency_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PgSQL: 注入 client 时不 import psycopg（即使 import 被强制失败也可 scan 成功）。"""

    real_import = builtins.__import__

    def fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if name == "psycopg":
            raise ModuleNotFoundError("No module named 'psycopg'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("should not read dsn from env when client is injected")

    monkeypatch.setattr(SkillsManager, "_source_dsn_from_env", boom)

    meta_cursor = FakePgCursor(
        rows=[
            {
                "id": 1,
                "account": "alice",
                "domain": "engineering",
                "skill_name": "python_testing",
                "description": "pytest patterns",
                "body_size": 0,
                "body_etag": "etag",
                "created_at": TS,
                "updated_at": "ts",
                "required_env_vars": [],
                "metadata": {},
                "scope": "repo",
            }
        ]
    )
    pg_client = FakePgClient([meta_cursor])

    mgr = _mk_manager(
        tmp_path,
        skills=_pgsql_skills_config({"schema": "public", "table": "skills"}),
        source_clients={"src-pg": pg_client},
    )

    report = mgr.scan()
    assert report.stats["skills_total"] == 1
    assert report.errors == []
