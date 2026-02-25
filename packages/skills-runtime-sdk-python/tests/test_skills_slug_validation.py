from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from skills_runtime.skills.manager import SkillsManager


def _mk_manager(tmp_path: Path, *, namespace: str, skills_root: Path) -> SkillsManager:
    """创建一个最小可 scan/preflight 的 SkillsManager（filesystem source）。"""

    return SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-1", "namespace": namespace, "sources": ["src-fs"]}],
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
    "namespace",
    [
        "a",  # segment too short
        "A1:b2",  # uppercase not allowed
        "-aa:bb",  # leading dash
        "aa-:bb",  # trailing dash
        "aa_bb:cc",  # underscore not allowed in segment
        "aa::bb",  # empty segment
        "a0:b1:c2:d3:e4:f5:g6:h7",  # 8 segments (max=7)
    ],
)
def test_config_rejects_invalid_namespace(namespace: str, tmp_path: Path) -> None:
    """配置层必须 fail-fast 拒绝非法的 skills.spaces[].namespace。"""

    skills_root = tmp_path / "skills_root"
    _write_skill(skills_root / "ok", name="python_testing")

    with pytest.raises(ValidationError):
        _mk_manager(tmp_path, namespace=namespace, skills_root=skills_root)


def test_config_rejects_legacy_space_fields(tmp_path: Path) -> None:
    """配置出现 legacy 二段式空间键字段时必须 fail-fast。"""

    with pytest.raises(ValidationError):
        SkillsManager(
            workspace_root=tmp_path,
            skills_config={
                "spaces": [
                    {
                        "id": "space-1",
                        "account": "alice",
                        "domain": "engineering",
                        "sources": ["src-fs"],
                    }
                ],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(tmp_path / "skills_root")}}],
            },
        )


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

    mgr = _mk_manager(tmp_path, namespace="alice:engineering", skills_root=skills_root)
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

    if skill_name == "a__b":
        skill_name = "a_"

    mgr = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-1", "namespace": "alice:engineering", "sources": ["src-mem"]}],
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
