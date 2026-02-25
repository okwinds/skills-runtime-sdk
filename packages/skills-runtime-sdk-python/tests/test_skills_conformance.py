from __future__ import annotations

from pathlib import Path

import pytest

from skills_runtime.skills.manager import SkillsManager
from skills_runtime.skills.mentions import extract_skill_mentions


def _write_skill(dir_path: Path, *, name: str, description: str, body: str = "body\n") -> Path:
    """写入测试 skill 文件。"""

    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / "SKILL.md"
    p.write_text(
        "\n".join(["---", f"name: {name}", f'description: "{description}"', "---", body.rstrip("\n"), ""]),
        encoding="utf-8",
    )
    return p


def _manager(tmp_path: Path, skills_root: Path) -> SkillsManager:
    """创建 SkillsManager fixture。"""

    return SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
        },
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", []),
        ("no mentions here", []),
        ("use $[alice:engineering].python_testing", [("alice", "engineering", "python_testing")]),
        ("$[alice:engineering].python_testing then do something", [("alice", "engineering", "python_testing")]),
        ("$[alice:engineering].python_testing: write something", [("alice", "engineering", "python_testing")]),
        ("text $[alice:engineering].redis_cache text", [("alice", "engineering", "redis_cache")]),
        ("请按 $[alice:engineering].python_testing 写一篇文章", [("alice", "engineering", "python_testing")]),
        ("ps -p $PPID -o command=", []),
        ("literal \\$python_testing is not a mention", []),
        ("$python_testing", []),
        ("$[a:engineering].python_testing", []),
        ("$[alice:e].python_testing", []),
        ("$[alice:engineering].a", []),
        ("$[alice].python_testing", []),
        ("$[alice:engineering]python_testing", []),
        ("$[alice:engineering].python testing", [("alice", "engineering", "python")]),
        ("$[alice:engineering].python:testing", [("alice", "engineering", "python")]),
        ("$[AlicE:engineering].python_testing", []),
        (
            "$[alice:engineering].python_testing,$[alice:engineering].redis_cache",
            [("alice", "engineering", "python_testing"), ("alice", "engineering", "redis_cache")],
        ),
    ],
)
def test_extract_skill_mentions_cases(
    text: str,
    expected: list[tuple[str, str, str]],
) -> None:
    """mention 解析回归。"""

    mentions = extract_skill_mentions(text)
    got = [(m.account, m.domain, m.skill_name) for m in mentions]
    assert got == expected


def test_skills_selection_respects_mention_order(tmp_path: Path) -> None:
    """解析输出顺序按 mention 顺序。"""

    root = tmp_path / "skills_root"
    _write_skill(root / "a", name="python_testing", description="a")
    _write_skill(root / "b", name="redis_cache", description="b")
    mgr = _manager(tmp_path, root)
    mgr.scan()

    selected = mgr.resolve_mentions("$[alice:engineering].redis_cache then $[alice:engineering].python_testing")
    assert [s.skill_name for s, _m in selected] == ["redis_cache", "python_testing"]


def test_injected_skill_envelope_matches_minimal_shape(tmp_path: Path) -> None:
    """注入块结构稳定。"""

    root = tmp_path / "skills_root"
    _write_skill(root / "a", name="python_testing", description="a", body="hello\n")
    mgr = _manager(tmp_path, root)
    skills = mgr.scan()
    assert skills

    injected = mgr.render_injected_skill(skills[0], source="mention", mention_text="$[alice:engineering].python_testing")
    assert injected.startswith("<skill>\n")
    assert "<name>python_testing</name>" in injected
    assert "<path>" in injected and "</path>" in injected
    assert injected.rstrip().endswith("</skill>")


def test_scan_options_depth_dot_entries_and_limit(tmp_path: Path) -> None:
    """scan 扩展参数兼容：max_depth/ignore_dot_entries/max_dirs_per_root。"""

    root = tmp_path / "skills_root"
    _write_skill(root / "a" / "b" / "c" / "d", name="deep_skill", description="x")
    _write_skill(root / ".hidden" / "s", name="hidden_skill", description="x")

    mgr_depth = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(root)}}],
            "scan": {"max_depth": 1, "ignore_dot_entries": True, "max_dirs_per_root": 100},
        },
    )
    report_depth = mgr_depth.scan()
    assert all(s.skill_name != "deep_skill" for s in report_depth.skills)
    assert all(s.skill_name != "hidden_skill" for s in report_depth.skills)

    mgr_limit = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(root)}}],
            "scan": {"max_depth": 10, "ignore_dot_entries": False, "max_dirs_per_root": 1},
        },
    )
    report_limit = mgr_limit.scan()
    assert any(e.code == "SKILL_SCAN_METADATA_INVALID" for e in report_limit.errors)
