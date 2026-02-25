from __future__ import annotations

from pathlib import Path

import pytest

from skills_runtime.core.errors import FrameworkError
from skills_runtime.skills.loader import load_skill_from_path
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.skills.mentions import extract_skill_mentions


def _write_skill(dir_path: Path, *, name: str, description: str, body: str = "body\n") -> Path:
    """写入 skill fixture。"""

    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / "SKILL.md"
    p.write_text(
        "\n".join(["---", f"name: {name}", f'description: "{description}"', "---", body.rstrip("\n"), ""]),
        encoding="utf-8",
    )
    return p


def _manager(tmp_path: Path, root: Path) -> SkillsManager:
    """创建 SkillsManager fixture。"""

    return SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "namespace": "alice:engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(root)}}],
        },
    )


def test_extract_mentions_ignores_common_env_vars() -> None:
    mentions = extract_skill_mentions("use $PATH and $[alice:engineering].python_testing")
    assert any(m.skill_name == "python_testing" for m in mentions)


@pytest.mark.parametrize(
    "text,expected_namespaces",
    [
        ("use $[web].article_writer", ["web"]),  # 1 segment
        (
            "use $[a0:b1:c2:d3:e4:f5:g6].article_writer",
            ["a0:b1:c2:d3:e4:f5:g6"],  # 7 segments
        ),
        ("use $[a0:b1:c2:d3:e4:f5:g6:h7].article_writer", []),  # 8 segments rejected
        ("use $[a:bb].article_writer", []),  # segment min length=2
    ],
)
def test_extract_mentions_namespace_segment_bounds(text: str, expected_namespaces: list[str]) -> None:
    """mention 解析必须满足 namespace 段数/segment 最小长度约束。"""

    mentions = extract_skill_mentions(text)
    assert [m.namespace for m in mentions] == expected_namespaces


def test_extract_mentions_namespace_is_order_sensitive() -> None:
    """namespace 必须按顺序区分，不可交换等价。"""

    mentions = extract_skill_mentions("use $[a0:b1].article_writer and $[b1:a0].article_writer")
    assert [m.namespace for m in mentions] == ["a0:b1", "b1:a0"]


def test_extract_mentions_ignores_trailing_bracket_typos() -> None:
    """
    自由文本提取必须容错：对形如 `$[a:b].x] ...` 的“粘贴括号错位”应忽略该 token，
    避免误注入/误执行，并且不得抛出格式错误中断 run。
    """

    mentions = extract_skill_mentions("use $[a0:b1].python_testing] and $[a0:b1].redis_cache")
    assert [m.mention_text for m in mentions] == ["$[a0:b1].redis_cache"]


def test_load_skill_parses_openai_yaml_env_var_dependencies(tmp_path: Path) -> None:
    skill_dir = tmp_path / "s1"
    p = _write_skill(skill_dir, name="python_testing", description="d")
    agents_dir = skill_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "openai.yaml").write_text(
        "\n".join(
            [
                "dependencies:",
                "  tools:",
                '    - type: "env_var"',
                '      value: "OPENAI_API_KEY"',
                '    - type: "env_var"',
                '      value: "OPENAI_API_KEY"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    skill = load_skill_from_path(p)
    assert skill.required_env_vars == ["OPENAI_API_KEY"]


def test_skills_manager_scan_and_resolve_mentions(tmp_path: Path) -> None:
    root = tmp_path / "skills_root"
    _write_skill(root / "a", name="python_testing", description="a")
    _write_skill(root / "b", name="redis_cache", description="b")

    mgr = _manager(tmp_path, root)
    skills = mgr.scan()
    assert [s.skill_name for s in skills] == ["python_testing", "redis_cache"]

    selected = mgr.resolve_mentions("use $[alice:engineering].python_testing please")
    assert len(selected) == 1
    assert selected[0][0].skill_name == "python_testing"


@pytest.mark.parametrize(
    "text,expected,error_code",
    [
        ("hello world", [], None),
        ("use $[alice:engineering].python_testing", ["python_testing"], None),
        ("use $[alice:engineering].does_not_exist", [], "SKILL_UNKNOWN"),
        (
            "use $[alice:engineering].redis_cache then $[alice:engineering].python_testing",
            ["redis_cache", "python_testing"],
            None,
        ),
        (
            "use $[alice:engineering].python_testing,$[alice:engineering].python_testing",
            ["python_testing"],
            None,
        ),
        ("use $[alice:ops].python_testing", [], "SKILL_SPACE_NOT_CONFIGURED"),
        ("use $python_testing", [], None),
    ],
)
def test_resolve_mentions_cases(tmp_path: Path, text: str, expected: list[str], error_code: str | None) -> None:
    root = tmp_path / "skills_root"
    _write_skill(root / "a", name="python_testing", description="a")
    _write_skill(root / "b", name="redis_cache", description="b")

    mgr = _manager(tmp_path, root)
    mgr.scan()

    if error_code is not None:
        with pytest.raises(FrameworkError) as exc_info:
            mgr.resolve_mentions(text)
        assert exc_info.value.code == error_code
        return

    selected = mgr.resolve_mentions(text)
    assert [s.skill_name for s, _m in selected] == expected


@pytest.mark.parametrize(
    "source,mention_text",
    [
        ("mention", None),
        ("mention", "$[alice:engineering].python_testing"),
        ("ui_select", None),
        ("ui_select", "$[alice:engineering].python_testing"),
        ("test", "x"),
        ("", ""),
        ("MENTION", "x"),
        ("other", "<tag>"),
    ],
)
def test_render_injected_skill_ignores_source_and_mention_text(
    tmp_path: Path, source: str, mention_text: str | None
) -> None:
    root = tmp_path / "skills_root"
    _write_skill(root / "a", name="python_testing", description="a", body="hello\n")
    mgr = _manager(tmp_path, root)
    skills = mgr.scan()
    assert len(skills) == 1

    injected = mgr.render_injected_skill(skills[0], source=source, mention_text=mention_text)
    assert injected.startswith("<skill>\n")
    assert "<name>python_testing</name>" in injected
    assert "<path>" in injected and "</path>" in injected
    assert injected.rstrip().endswith("</skill>")
