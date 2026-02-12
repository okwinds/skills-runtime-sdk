from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

import pytest

from agent_sdk.config.loader import AgentSdkSkillsConfig, load_config_dicts
from agent_sdk.skills.manager import SkillsManager


def _base_config(skills: dict[str, Any]) -> dict[str, Any]:
    """构造最小可校验配置（只覆盖本文件关注的 skills 字段）。"""

    return {
        "config_version": 1,
        "run": {"max_steps": 5},
        "llm": {"base_url": "http://example.test/v1", "api_key_env": "OPENAI_API_KEY"},
        "models": {"planner": "planner-x", "executor": "executor-x"},
        "skills": skills,
    }


def _build_skills_config(skills: dict[str, Any]) -> AgentSdkSkillsConfig:
    """从 dict 构造 `AgentSdkSkillsConfig`，确保 extra 字段可进入 model_extra。"""

    return load_config_dicts([_base_config(skills)]).skills


def _mk_manager(tmp_path: Path, *, skills: dict[str, Any]) -> SkillsManager:
    """构建 SkillsManager 测试实例。"""

    return SkillsManager(workspace_root=tmp_path, skills_config=_build_skills_config(skills))


def _find_issues(issues: Iterable[Any], *, code: str) -> List[Any]:
    """按 code 过滤 issues。"""

    return [it for it in issues if getattr(it, "code", None) == code]


def _assert_has_issue(issues: List[Any], *, code: str, path: str) -> None:
    """断言至少存在一个符合 code/path 的 issue。"""

    hits = [it for it in issues if getattr(it, "code", None) == code and getattr(it, "details", {}).get("path") == path]
    assert hits, f"missing issue code={code} path={path}; got={[(i.code, i.details.get('path')) for i in issues]}"


def _write_fs_skill(root: Path, *, name: str, description: str) -> Path:
    """写入一个 filesystem skill fixture（最小 frontmatter）。"""

    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f'description: \"{description}\"',
                "---",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return skill_md


def test_preflight_valid_config_returns_empty_list(tmp_path: Path) -> None:
    """valid config → issues == []。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )
    assert mgr.preflight() == []


def test_preflight_legacy_skills_roots_is_error(tmp_path: Path) -> None:
    """legacy skills.roots → SKILL_CONFIG_LEGACY_ROOTS_UNSUPPORTED。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "roots": ["./skills"],
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_LEGACY_ROOTS_UNSUPPORTED", path="skills.roots")


def test_preflight_legacy_skills_mode_is_error(tmp_path: Path) -> None:
    """legacy skills.mode != explicit → SKILL_CONFIG_LEGACY_MODE_UNSUPPORTED。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "mode": "auto",
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_LEGACY_MODE_UNSUPPORTED", path="skills.mode")


def test_preflight_unknown_top_level_key_is_error(tmp_path: Path) -> None:
    """skills 顶层未知 key → SKILL_CONFIG_UNKNOWN_TOP_LEVEL_KEY。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "soruces": [],  # typo
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_UNKNOWN_TOP_LEVEL_KEY", path="skills.soruces")


def test_preflight_unknown_space_key_is_error(tmp_path: Path) -> None:
    """space 未知 key → SKILL_CONFIG_UNKNOWN_NESTED_KEY。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [
                {
                    "id": "space-eng",
                    "account": "alice",
                    "domain": "engineering",
                    "sources": ["src-fs"],
                    "accout": "typo",  # extra
                }
            ],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_UNKNOWN_NESTED_KEY", path="skills.spaces[0].accout")


def test_preflight_space_references_unknown_source_id(tmp_path: Path) -> None:
    """space 引用不存在 source.id → SKILL_CONFIG_SPACE_SOURCE_NOT_FOUND。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["not-exists"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_SPACE_SOURCE_NOT_FOUND", path="skills.spaces[0].sources[0]")


def test_preflight_duplicate_space_id(tmp_path: Path) -> None:
    """重复 space.id → SKILL_CONFIG_DUPLICATE_SPACE_ID。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [
                {"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]},
                {"id": "space-eng", "account": "bob", "domain": "engineering", "sources": ["src-fs"]},
            ],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_DUPLICATE_SPACE_ID", path="skills.spaces[1].id")


def test_preflight_duplicate_source_id(tmp_path: Path) -> None:
    """重复 source.id → SKILL_CONFIG_DUPLICATE_SOURCE_ID。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [
                {"id": "src-fs", "type": "filesystem", "options": {"root": "skills-a"}},
                {"id": "src-fs", "type": "filesystem", "options": {"root": "skills-b"}},
            ],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_DUPLICATE_SOURCE_ID", path="skills.sources[1].id")


def test_preflight_unknown_source_type(tmp_path: Path) -> None:
    """unknown source.type → SKILL_CONFIG_UNKNOWN_SOURCE_TYPE。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-x"]}],
            "sources": [{"id": "src-x", "type": "s3", "options": {"bucket": "b"}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_UNKNOWN_SOURCE_TYPE", path="skills.sources[0].type")


def test_preflight_filesystem_missing_root(tmp_path: Path) -> None:
    """filesystem 缺 options.root → SKILL_CONFIG_MISSING_REQUIRED_OPTION。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_MISSING_REQUIRED_OPTION", path="skills.sources[0].options.root")


def test_preflight_redis_invalid_dsn_env_name(tmp_path: Path) -> None:
    """redis dsn_env 非法 → SKILL_CONFIG_INVALID_ENV_VAR_NAME。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-redis"]}],
            "sources": [
                {
                    "id": "src-redis",
                    "type": "redis",
                    "options": {"dsn_env": "redis_url", "key_prefix": "skills:"},
                }
            ],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_INVALID_ENV_VAR_NAME", path="skills.sources[0].options.dsn_env")


def test_preflight_pgsql_missing_schema_and_table(tmp_path: Path) -> None:
    """pgsql 缺 schema/table → SKILL_CONFIG_MISSING_REQUIRED_OPTION。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-db"]}],
            "sources": [{"id": "src-db", "type": "pgsql", "options": {"dsn_env": "PG_DSN"}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_MISSING_REQUIRED_OPTION", path="skills.sources[0].options.schema")
    _assert_has_issue(issues, code="SKILL_CONFIG_MISSING_REQUIRED_OPTION", path="skills.sources[0].options.table")


def test_preflight_guard_scan_semantics_unchanged(tmp_path: Path) -> None:
    """护栏：新增 preflight 不应改变 scan 的既有容错语义。"""

    fs_root = tmp_path / "skills"
    _write_fs_skill(fs_root, name="python_testing", description="pytest patterns")

    mgr = _mk_manager(
        tmp_path,
        skills={
            "soruces": [],  # unknown top-level key（preflight error），但 scan 应保持尽力而为
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        },
    )

    # preflight 报错不应影响 scan（scan 不应隐式调用 preflight）
    issues = mgr.preflight()
    assert _find_issues(issues, code="SKILL_CONFIG_UNKNOWN_TOP_LEVEL_KEY")

    report = mgr.scan()
    assert report.stats["skills_total"] == 1
    assert report.skills[0].skill_name == "python_testing"


def test_preflight_versioning_and_strictness_unknown_keys_are_warnings(tmp_path: Path) -> None:
    """versioning/strictness 未知字段 → 只产生 warning（不得导致 preflight 失败）。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "versioning": {"enabled": False, "strategy": "TODO", "rollout": {"pct": 10}},
            "strictness": {"unknown_mention": "error", "extra_flag": True},
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )
    issues = mgr.preflight()
    assert issues, "expected warnings for versioning/strictness extras"
    assert all(getattr(it, "details", {}).get("level") == "warning" for it in issues)


def test_preflight_in_memory_missing_namespace(tmp_path: Path) -> None:
    """in-memory 缺 options.namespace → SKILL_CONFIG_MISSING_REQUIRED_OPTION。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-mem"]}],
            "sources": [{"id": "src-mem", "type": "in-memory", "options": {}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code="SKILL_CONFIG_MISSING_REQUIRED_OPTION", path="skills.sources[0].options.namespace")


@pytest.mark.parametrize(
    "scan_value, expected_code, expected_path",
    [
        ("always", "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan"),
        ({"unknown_key": 1}, "SKILL_CONFIG_UNKNOWN_SCAN_OPTION", "skills.scan.unknown_key"),
        ({"refresh_policy": "sometimes"}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.refresh_policy"),
        ({"refresh_policy": 1}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.refresh_policy"),
        ({"ttl_sec": "300"}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.ttl_sec"),
        ({"ttl_sec": 0}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.ttl_sec"),
        ({"max_frontmatter_bytes": "65536"}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.max_frontmatter_bytes"),
        ({"max_frontmatter_bytes": 0}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.max_frontmatter_bytes"),
        ({"max_depth": "deep"}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.max_depth"),
        ({"max_depth": -1}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.max_depth"),
        ({"max_dirs_per_root": "10"}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.max_dirs_per_root"),
        ({"max_dirs_per_root": -1}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.max_dirs_per_root"),
        ({"ignore_dot_entries": "true"}, "SKILL_CONFIG_INVALID_SCAN_OPTION", "skills.scan.ignore_dot_entries"),
    ],
)
def test_preflight_skills_scan_validation(tmp_path: Path, scan_value: Any, expected_code: str, expected_path: str) -> None:
    """skills.scan：未知字段/类型/范围错误 → SKILL_CONFIG_*_SCAN_OPTION。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "scan": scan_value,
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )
    issues = mgr.preflight()
    _assert_has_issue(issues, code=expected_code, path=expected_path)


def test_runtime_skills_scan_invalid_ints_do_not_raise_value_error(tmp_path: Path) -> None:
    """runtime：非法 scan int 值不应在构造期触发 ValueError（应回退默认值）。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "scan": {
                "max_depth": "deep",
                "max_dirs_per_root": "a-lot",
                "max_frontmatter_bytes": "huge",
            },
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )

    assert mgr._scan_options["max_depth"] == 99
    assert mgr._scan_options["max_dirs_per_root"] == 100000
    assert mgr._scan_options["max_frontmatter_bytes"] == 65536
