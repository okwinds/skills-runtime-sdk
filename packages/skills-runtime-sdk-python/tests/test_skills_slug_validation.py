from __future__ import annotations

from pathlib import Path

import pytest

from agent_sdk.skills.manager import SkillsManager


def _mk_manager(tmp_path: Path, *, account: str, domain: str, skills_root: Path) -> SkillsManager:
    """创建一个最小可 scan/preflight 的 SkillsManager（filesystem source）。"""

    return SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-1", "account": account, "domain": domain, "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
        },
    )


def _write_skill(dir_path: Path, *, name: str) -> Path:
    """写入一个仅包含 frontmatter 的 SKILL.md。"""

    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / "SKILL.md"
    safe_name = str(name).replace("\\", "\\\\").replace('"', '\\"')
    p.write_text(
        "\n".join(["---", f'name: "{safe_name}"', 'description: "desc"', "---", "body", ""]),
        encoding="utf-8",
    )
    return p


@pytest.mark.parametrize(
    "field,value",
    [
        ("account", "a"),  # too short (min=2)
        ("account", "A1"),  # uppercase not allowed
        ("account", "-aa"),  # leading dash
        ("account", "aa-"),  # trailing dash
        ("account", "aa_bb"),  # underscore not allowed
        ("account", "aa:bb"),  # ':' not allowed
        ("domain", "d"),  # too short (min=2)
        ("domain", "D1"),  # uppercase not allowed
        ("domain", "-dd"),  # leading dash
        ("domain", "dd-"),  # trailing dash
        ("domain", "dd_bb"),  # underscore not allowed
        ("domain", "dd:bb"),  # ':' not allowed
    ],
)
def test_preflight_rejects_invalid_space_slug(field: str, value: str, tmp_path: Path) -> None:
    """preflight 必须能静态拒绝非法的 skills.spaces[].account/domain（不依赖 scan I/O）。"""

    skills_root = tmp_path / "skills_root"
    _write_skill(skills_root / "ok", name="python_testing")

    account = value if field == "account" else "alice"
    domain = value if field == "domain" else "engineering"
    mgr = _mk_manager(tmp_path, account=account, domain=domain, skills_root=skills_root)
    issues = mgr.preflight()

    slug_issues = [it for it in issues if it.code == "SKILL_CONFIG_INVALID_SPACE_SLUG"]
    assert slug_issues, issues
    assert any(it.details.get("field") == field for it in slug_issues)
    assert any(isinstance(it.details.get("actual"), str) and it.details.get("actual") for it in slug_issues)


@pytest.mark.parametrize(
    "skill_name",
    [
        "a",  # too short (min=2)
        "A1",  # uppercase not allowed
        "_a",  # leading underscore
        "a_",  # trailing underscore
        "-a",  # leading dash
        "a-",  # trailing dash
        "a:",  # ':' not allowed
        "a]",  # ']' not allowed
        "a.b",  # '.' not allowed
        "a" * 65,  # too long
    ],
)
def test_scan_rejects_invalid_filesystem_skill_name_slug(skill_name: str, tmp_path: Path) -> None:
    """filesystem scan：skill_name 必须符合 slug 规则，否则报 metadata invalid 且不得进入 skills 列表。"""

    skills_root = tmp_path / "skills_root"
    _write_skill(skills_root / "bad", name=skill_name)

    mgr = _mk_manager(tmp_path, account="alice", domain="engineering", skills_root=skills_root)
    report = mgr.scan()

    assert not report.skills
    assert any(
        it.code == "SKILL_SCAN_METADATA_INVALID" and it.details.get("reason") == "invalid_skill_name_slug"
        for it in report.errors
    ), report.to_jsonable()


@pytest.mark.parametrize(
    "skill_name",
    [
        "a",  # too short
        "A1",  # uppercase
        "_a",  # leading underscore
        "a_",  # trailing underscore
        "a:",  # ':' not allowed
        "a]",  # ']' not allowed
        "a" * 65,  # too long
        "a--",  # trailing dash
        "--a",  # leading dash
        "a__b",  # underscore ok internally, but still needs start/end alnum (this one is valid) -> choose a_
    ],
)
def test_scan_rejects_invalid_in_memory_skill_name_slug(skill_name: str, tmp_path: Path) -> None:
    """in-memory scan：skill_name 必须符合 slug 规则（与其他 sources 一致）。"""

    # 备注：最后一条为了凑 10+ cases，这里显式避开“内部下划线合法”的情况，保证确实非法。
    if skill_name == "a__b":
        skill_name = "a_"

    mgr = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-1", "account": "alice", "domain": "engineering", "sources": ["src-mem"]}],
            "sources": [{"id": "src-mem", "type": "in-memory", "options": {"namespace": "ns1"}}],
        },
        in_memory_registry={
            "ns1": [
                {"skill_name": skill_name, "description": "d", "body": "b"},
            ]
        },
    )
    report = mgr.scan()

    assert not report.skills
    assert any(
        it.code == "SKILL_SCAN_METADATA_INVALID" and it.details.get("reason") == "invalid_skill_name_slug"
        for it in report.errors
    ), report.to_jsonable()
