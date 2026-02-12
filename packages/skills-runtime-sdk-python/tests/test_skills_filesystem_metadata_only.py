from __future__ import annotations

from pathlib import Path

import pytest

from agent_sdk.skills.manager import SkillsManager


def _write_skill(
    dir_path: Path,
    *,
    name: str,
    description: str,
    body: str = "body\n",
    extra_frontmatter_lines: list[str] | None = None,
) -> Path:
    """写入一个 filesystem SKILL.md fixture（可附带额外 frontmatter 行）。"""

    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / "SKILL.md"
    fm = ["---", f"name: {name}", f'description: "{description}"']
    fm.extend(list(extra_frontmatter_lines or []))
    fm.append("---")
    p.write_text("\n".join([*fm, body.rstrip("\n"), ""]), encoding="utf-8")
    return p


def _manager(tmp_path: Path, root: Path, *, scan: dict | None = None) -> SkillsManager:
    """创建一个仅包含 filesystem source 的 SkillsManager。"""

    cfg: dict = {
        "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
        "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(root)}}],
    }
    if scan is not None:
        cfg["scan"] = dict(scan)
    return SkillsManager(workspace_root=tmp_path, skills_config=cfg)


def test_filesystem_scan_is_metadata_only_does_not_call_path_read_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """scan 阶段必须 frontmatter-only：不得对 SKILL.md 调用 Path.read_text()。"""

    root = tmp_path / "skills_root"
    _write_skill(root / "s1", name="python_testing", description="d", body="## BODY\n" + ("x" * 1024) + "\n")

    real_read_text = Path.read_text

    def guarded_read_text(self: Path, *args, **kwargs) -> str:
        if self.name == "SKILL.md":
            raise AssertionError("filesystem scan must not call Path.read_text() on SKILL.md")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text, raising=True)

    mgr = _manager(tmp_path, root)
    report = mgr.scan()

    assert report.errors == []
    assert [s.skill_name for s in report.skills] == ["python_testing"]
    assert "body_markdown" not in report.skills[0].metadata


def test_filesystem_scan_respects_max_frontmatter_bytes(tmp_path: Path) -> None:
    """frontmatter 超过 max_frontmatter_bytes 必须在 scan 阶段报错（metadata invalid）。"""

    root = tmp_path / "skills_root"
    # 保证 frontmatter 足够大（多行 + 超长字段），并且仍有 closing '---'
    _write_skill(
        root / "s1",
        name="python_testing",
        description="d",
        extra_frontmatter_lines=[
            f'notes: "{("y" * 256)}"',
        ],
    )

    mgr = _manager(tmp_path, root, scan={"max_frontmatter_bytes": 64})
    report = mgr.scan()

    assert [e.code for e in report.errors] == ["SKILL_SCAN_METADATA_INVALID"]
    details = dict(report.errors[0].details)
    assert details.get("reason") == "frontmatter_too_large"


def test_filesystem_scan_unterminated_frontmatter_reports_error(tmp_path: Path) -> None:
    """frontmatter 未闭合（缺少第 2 个 ---）必须在 scan 阶段报错。"""

    root = tmp_path / "skills_root"
    skill_dir = root / "s1"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("\n".join(["---", "name: x", 'description: "d"', ""]), encoding="utf-8")

    mgr = _manager(tmp_path, root, scan={"max_frontmatter_bytes": 1024})
    report = mgr.scan()

    assert [e.code for e in report.errors] == ["SKILL_SCAN_METADATA_INVALID"]
    details = dict(report.errors[0].details)
    assert details.get("reason") == "frontmatter_unterminated"

