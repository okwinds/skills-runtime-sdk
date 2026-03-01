from __future__ import annotations

import os
from pathlib import Path

import pytest

from skills_runtime.skills.manager import SkillsManager


def _write_skill(bundle_root: Path, *, name: str) -> None:
    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                'description: "d"',
                "---",
                "body",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _mk_manager(*, workspace_root: Path, skills_root: Path) -> SkillsManager:
    mgr = SkillsManager(
        workspace_root=workspace_root,
        skills_config={
            "spaces": [{"id": "space-eng", "namespace": "alice:engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
        },
    )
    mgr.scan()
    return mgr


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows in this SDK")
def test_filesystem_scan_does_not_traverse_symlinked_dirs_pointing_outside_root(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 1.4）：
    filesystem scan 不得通过目录 symlink traversal 扩展扫描范围到 root 之外。
    """

    skills_root = tmp_path / "skills_root"
    outside = tmp_path / "outside_root"

    _write_skill(outside / "evil_skill", name="evil_skill")

    skills_root.mkdir(parents=True, exist_ok=True)
    link = skills_root / "link_outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlink not available in test environment: {e}")

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root)
    names = sorted(s.skill_name for s in mgr.list_skills())
    assert "evil_skill" not in names


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows in this SDK")
def test_filesystem_scan_rejects_resolved_skill_md_path_outside_root_with_structured_issue(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 1.5）：
    对发现的 SKILL.md，resolve 后若落到 root 外，必须 fail-closed，
    并在 scan_report.errors 中产出可机器消费的 issue（reason=path_escape）。
    """

    skills_root = tmp_path / "skills_root"
    outside = tmp_path / "outside_root"
    outside.mkdir(parents=True, exist_ok=True)

    outside_skill_md = (outside / "SKILL.md").resolve()
    outside_skill_md.write_text(
        "\n".join(["---", "name: escaped_skill", 'description: "d"', "---", "body", ""]),
        encoding="utf-8",
    )

    bundle = skills_root / "escaped_skill"
    bundle.mkdir(parents=True, exist_ok=True)
    link = bundle / "SKILL.md"
    try:
        link.symlink_to(outside_skill_md)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlink not available in test environment: {e}")

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root)
    names = sorted(s.skill_name for s in mgr.list_skills())
    assert "escaped_skill" not in names

    report = mgr.last_scan_report
    assert report is not None
    issues = list(report.errors or [])
    assert any(
        (getattr(i, "code", None) == "SKILL_SCAN_METADATA_INVALID")
        and isinstance(getattr(i, "details", None), dict)
        and (i.details or {}).get("reason") == "path_escape"
        for i in issues
    ), "expected a structured scan issue with details.reason=path_escape"


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows in this SDK")
def test_filesystem_body_loader_fails_closed_on_root_escape_after_scan(tmp_path: Path) -> None:
    """
    回归（close-harden-safety-test-gaps / regression-test-guardrails）：
    `body_loader` 必须在 TOCTOU（scan 后 SKILL.md 被替换为指向 root 外的 symlink）时 fail-closed，
    不得读取 root 外文件内容。
    """

    skills_root = tmp_path / "skills_root"
    outside = tmp_path / "outside_root"
    outside.mkdir(parents=True, exist_ok=True)

    _write_skill(skills_root / "good_skill", name="good_skill")
    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root)

    skills = list(mgr.list_skills())
    good = next(s for s in skills if s.skill_name == "good_skill")

    outside_body = outside / "SKILL.md"
    outside_body.write_text(
        "\n".join(["---", "name: outside", 'description: "d"', "---", "SHOULD_NOT_READ", ""]),
        encoding="utf-8",
    )

    skill_md = skills_root / "good_skill" / "SKILL.md"
    try:
        skill_md.unlink()
        skill_md.symlink_to(outside_body)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlink not available in test environment: {e}")

    with pytest.raises(PermissionError):
        _ = good.body_loader()
