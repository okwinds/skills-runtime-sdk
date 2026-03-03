"""Task 3.3：验证 PromptTemplates 模板缓存和 list_skills 单次调用。"""

from __future__ import annotations

import unittest.mock as mock
from pathlib import Path
from typing import List

from skills_runtime.prompts.manager import PromptManager, PromptTemplates
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.tools.protocol import ToolSpec


# ─── PromptTemplates 文件缓存测试 ────────────────────────────────────────────

def test_prompt_templates_load_from_file_reads_only_once(tmp_path: Path) -> None:
    """PromptTemplates.load() 多次调用时，文件 MUST 只读一次（首次加载后缓存）。"""
    system_file = tmp_path / "system.md"
    developer_file = tmp_path / "developer.md"
    system_file.write_text("You are a test agent.", encoding="utf-8")
    developer_file.write_text("Follow TDD.", encoding="utf-8")

    templates = PromptTemplates(
        system_path=system_file,
        developer_path=developer_file,
        name="test",
        version="1",
    )

    with mock.patch("skills_runtime.prompts.manager._read_text_file", wraps=lambda p: Path(p).read_text(encoding="utf-8")) as mock_read:
        # 第一次调用
        r1 = templates.load()
        # 第二次调用
        r2 = templates.load()
        # 第三次调用
        r3 = templates.load()

    assert r1 == r2 == r3
    # 只读了 2 次（system + developer），而非 6 次（每次调用各读一次）
    assert mock_read.call_count == 2, (
        f"期望 2 次文件读取（初始化时），实际 {mock_read.call_count} 次（缓存未生效）"
    )


def test_prompt_templates_load_from_text_never_reads_file(tmp_path: Path) -> None:
    """system_text/developer_text 直接提供时，不应读取文件。"""
    templates = PromptTemplates(
        system_text="SYS",
        developer_text="DEV",
        name="test",
        version="1",
    )

    with mock.patch("skills_runtime.prompts.manager._read_text_file") as mock_read:
        result = templates.load()
        result2 = templates.load()

    assert result == ("SYS", "DEV")
    assert result2 == ("SYS", "DEV")
    assert mock_read.call_count == 0


# ─── build_messages list_skills 单次调用测试 ─────────────────────────────────

def _make_skills_manager(tmp_path: Path) -> SkillsManager:
    skills_root = tmp_path / "skills"
    (skills_root / "s1").mkdir(parents=True)
    (skills_root / "s1" / "SKILL.md").write_text(
        "---\nname: demo_skill\ndescription: demo\n---\nbody\n",
        encoding="utf-8",
    )
    sm = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "sp", "namespace": "test:ns", "sources": ["src"]}],
            "sources": [{"id": "src", "type": "filesystem", "options": {"root": str(skills_root)}}],
        },
    )
    sm.scan()
    return sm


def test_build_messages_calls_list_skills_once(tmp_path: Path) -> None:
    """build_messages 对 skills_manager.list_skills 的调用次数 MUST 为 1。"""
    sm = _make_skills_manager(tmp_path)
    pm = PromptManager(
        templates=PromptTemplates(system_text="SYS", developer_text="DEV"),
        include_skills_list=True,
    )
    tools: List[ToolSpec] = []

    with mock.patch.object(sm, "list_skills", wraps=sm.list_skills) as mock_list:
        pm.build_messages(
            task="test task",
            cwd=str(tmp_path),
            tools=tools,
            skills_manager=sm,
            injected_skills=[],
            history=[],
        )

    assert mock_list.call_count == 1, (
        f"期望 list_skills 被调用 1 次，实际 {mock_list.call_count} 次（存在冗余调用）"
    )
