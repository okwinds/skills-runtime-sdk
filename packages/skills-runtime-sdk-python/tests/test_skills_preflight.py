from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

import pytest

from skills_runtime.config.loader import AgentSdkSkillsConfig, load_config_dicts
from skills_runtime.skills.manager import SkillsManager


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
    """从 dict 构造 `AgentSdkSkillsConfig`（严格 schema；未知/legacy 字段应 fail-fast）。"""

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
    """legacy skills.roots 必须被严格拒绝（fail-fast）。"""

    with pytest.raises(Exception):
        _build_skills_config(
            {
                "roots": ["./skills"],
                "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
            }
        )


def test_preflight_legacy_skills_mode_is_error(tmp_path: Path) -> None:
    """legacy skills.mode 必须被严格拒绝（fail-fast）。"""

    with pytest.raises(Exception):
        _build_skills_config(
            {
                "mode": "auto",
                "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
            }
        )


def test_preflight_unknown_top_level_key_is_error(tmp_path: Path) -> None:
    """skills 顶层未知 key 必须被严格拒绝（fail-fast）。"""

    with pytest.raises(Exception):
        _build_skills_config(
            {
                "soruces": [],  # typo
                "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
            }
        )


def test_preflight_unknown_space_key_is_error(tmp_path: Path) -> None:
    """space 未知 key 必须被严格拒绝（fail-fast）。"""

    with pytest.raises(Exception):
        _build_skills_config(
            {
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
            }
        )


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
    """护栏：非法配置必须在加载阶段 fail-fast（不再允许“尽力而为”的隐式容错）。"""

    with pytest.raises(Exception):
        _build_skills_config(
            {
                "soruces": [],  # typo
                "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
            }
        )


def test_preflight_versioning_and_strictness_unknown_keys_are_warnings(tmp_path: Path) -> None:
    """versioning/strictness 未知字段必须被严格拒绝（fail-fast）。"""

    with pytest.raises(Exception):
        _build_skills_config(
            {
                "versioning": {"enabled": False, "strategy": "TODO", "rollout": {"pct": 10}},
                "strictness": {"unknown_mention": "error", "extra_flag": True},
                "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
            }
        )


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
    "scan_value",
    [
        "always",
        {"unknown_key": 1},
        {"refresh_policy": "sometimes"},
        {"refresh_policy": 1},
        {"ttl_sec": "300"},
        {"ttl_sec": 0},
        {"max_frontmatter_bytes": "65536"},
        {"max_frontmatter_bytes": 0},
        {"max_depth": "deep"},
        {"max_depth": -1},
        {"max_dirs_per_root": "10"},
        {"max_dirs_per_root": -1},
        {"ignore_dot_entries": "true"},
    ],
)
def test_skills_scan_config_invalid_fails_fast(tmp_path: Path, scan_value: Any) -> None:
    """skills.scan：未知字段/类型/范围错误必须 fail-fast（由 schema 负责拒绝）。"""

    with pytest.raises(Exception):
        _build_skills_config(
            {
                "scan": scan_value,
                "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
            }
        )


def test_skills_scan_config_valid_is_applied_to_manager(tmp_path: Path) -> None:
    """runtime：显式 skills.scan 配置必须确定性生效。"""

    mgr = _mk_manager(
        tmp_path,
        skills={
            "scan": {
                "max_depth": 1,
                "max_dirs_per_root": 2,
                "max_frontmatter_bytes": 4096,
                "ignore_dot_entries": False,
                "refresh_policy": "ttl",
                "ttl_sec": 10,
            },
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
        },
    )

    assert mgr._scan_options["max_depth"] == 1
    assert mgr._scan_options["max_dirs_per_root"] == 2
    assert mgr._scan_options["max_frontmatter_bytes"] == 4096
    assert mgr._scan_options["ignore_dot_entries"] is False


def test_skills_manager_roots_constructor_argument_is_rejected(tmp_path: Path) -> None:
    """SkillsManager 不得再接受 roots 兼容构造参数。"""

    with pytest.raises(TypeError):
        SkillsManager(workspace_root=tmp_path, roots=[tmp_path])  # type: ignore[call-arg]
