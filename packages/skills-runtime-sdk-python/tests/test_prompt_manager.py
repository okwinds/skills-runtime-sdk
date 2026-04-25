from __future__ import annotations

from pathlib import Path

from skills_runtime.prompts.history import trim_history
from skills_runtime.prompts.manager import PromptManager, PromptTemplates
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.skills.models import Skill
from skills_runtime.tools.protocol import ToolSpec


def _write_skill(dir_path: Path, *, name: str, description: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "SKILL.md").write_text(
        "\n".join(["---", f"name: {name}", f'description: \"{description}\"', "---", "body", ""]),
        encoding="utf-8",
    )


def _make_skills_manager_with_two_skills(tmp_path: Path) -> tuple[SkillsManager, list]:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root / "mentioned", name="mentioned_skill", description="mentioned desc")
    _write_skill(skills_root / "unmentioned", name="unmentioned_skill", description="unmentioned desc")
    sm = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-gen", "namespace": "demo:writing", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
        },
    )
    skills = list(sm.scan())
    return sm, skills


def test_trim_history_by_max_messages_and_chars() -> None:
    hist = [
        {"role": "user", "content": "a" * 10},
        {"role": "assistant", "content": "b" * 10},
        {"role": "user", "content": "c" * 10},
    ]

    kept, dropped = trim_history(hist, max_messages=2, max_chars=1000)
    assert [m["content"] for m in kept] == ["b" * 10, "c" * 10]
    assert dropped == 1

    kept2, dropped2 = trim_history(hist, max_messages=10, max_chars=15)
    assert len(kept2) == 1
    assert kept2[0]["content"] == "c" * 10
    assert dropped2 == 2


def test_prompt_manager_message_order_includes_skills_list_and_injections(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root / "s1", name="python_testing", description="pytest patterns")

    sm = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "namespace": "alice:engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
        },
    )
    skills = sm.scan()
    assert len(skills) == 1

    tools = [
        ToolSpec(
            name="file_read",
            description="read",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        )
    ]

    pm = PromptManager(
        templates=PromptTemplates(system_text="SYS {{cwd}}", developer_text="DEV {{tools}}", name="t", version="1"),
        include_skills_list=True,
        history_max_messages=50,
        history_max_chars=100000,
    )

    injected = [(skills[0], "mention", "$[alice:engineering].python_testing")]
    history = [{"role": "assistant", "content": "prev"}]

    messages, debug = pm.build_messages(
        task="do something",
        cwd=str(tmp_path),
        tools=tools,
        skills_manager=sm,
        injected_skills=injected,
        history=history,
        user_input=None,
    )

    roles = [m["role"] for m in messages]
    assert roles[0] == "system"
    assert roles[1] == "user"  # skills list section
    assert roles[2] == "user"  # injected skill
    assert roles[3] == "assistant"  # history
    assert roles[-1] == "user"  # task

    assert "Available skills (mention via $[namespace].skill_name):" in messages[1]["content"]
    assert "$[alice:engineering].python_testing" in messages[1]["content"]

    assert debug["templates"][0]["name"] == "t"
    assert debug["skills_count"] == 1
    assert debug["tools_count"] == 1


def test_generation_direct_profile_builds_noise_free_messages(tmp_path: Path) -> None:
    sm, skills = _make_skills_manager_with_two_skills(tmp_path)
    mentioned = next(s for s in skills if s.skill_name == "mentioned_skill")
    unmentioned = next(s for s in skills if s.skill_name == "unmentioned_skill")
    tools = [
        ToolSpec(
            name="file_read",
            description="read",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        )
    ]
    task = "Write copy with $[demo:writing].mentioned_skill"

    pm = PromptManager(
        templates=PromptTemplates(system_text="Direct generation system.", developer_text=""),
        profile="generation_direct",
        include_skills_list=False,
        skill_injection_mode="explicit_only",
        skill_render="body",
        history_mode="none",
        tools_exposure="none",
    )

    messages, debug = pm.build_messages(
        task=task,
        cwd=str(tmp_path),
        tools=tools,
        skills_manager=sm,
        injected_skills=[
            (mentioned, "mention", "$[demo:writing].mentioned_skill"),
            (unmentioned, "auto", None),
        ],
        history=[{"role": "assistant", "content": "resume summary"}],
    )

    joined = "\n".join(str(m["content"]) for m in messages)
    injected_skill_messages = [m for m in messages if "<skill>" in str(m["content"])]

    assert "Follow a spec-driven + TDD workflow" not in messages[0]["content"]
    assert "[Developer Policy]" not in messages[0]["content"]
    assert not any("Available skills" in str(m["content"]) for m in messages)
    assert "resume summary" not in joined
    assert "unmentioned_skill" not in joined
    assert len(injected_skill_messages) == 1
    assert debug["profile"] == "generation_direct"
    assert debug["history_mode"] == "none"
    assert debug["tools_exposure"] == "none"
    assert debug["tools_count"] == 0


def test_skill_render_summary_does_not_read_skill_body(tmp_path: Path) -> None:
    sm, _skills = _make_skills_manager_with_two_skills(tmp_path)

    def _fail_body_loader() -> str:
        raise AssertionError("summary render must not read skill body")

    mentioned = Skill(
        space_id="space-gen",
        source_id="src-fs",
        namespace="demo:writing",
        skill_name="mentioned_skill",
        description="mentioned desc",
        locator="memory://mentioned",
        path=None,
        body_size=None,
        body_loader=_fail_body_loader,
        required_env_vars=[],
        metadata={},
    )

    pm = PromptManager(
        templates=PromptTemplates(system_text="SYS", developer_text=""),
        profile="structured_transform",
        include_skills_list=False,
        skill_injection_mode="explicit_only",
        skill_render="summary",
        history_mode="none",
        tools_exposure="none",
    )

    messages, _debug = pm.build_messages(
        task="Transform with $[demo:writing].mentioned_skill",
        cwd=str(tmp_path),
        tools=[],
        skills_manager=sm,
        injected_skills=[(mentioned, "mention", "$[demo:writing].mentioned_skill")],
        history=[],
    )

    joined = "\n".join(str(m["content"]) for m in messages)
    assert "Skill summary:" in joined
    assert "mentioned desc" in joined
    assert "<skill>" not in joined


def test_tools_exposure_explicit_only_filters_to_mentioned_tool(tmp_path: Path) -> None:
    sm, _skills = _make_skills_manager_with_two_skills(tmp_path)
    tools = [
        ToolSpec(name="file_read", description="read", parameters={"type": "object", "properties": {}}),
        ToolSpec(name="shell_exec", description="exec", parameters={"type": "object", "properties": {}}),
    ]
    pm = PromptManager(
        templates=PromptTemplates(system_text="Tools:\n{{tools}}", developer_text=""),
        profile="default_agent",
        include_skills_list=False,
        tools_exposure="explicit_only",
    )

    messages, debug = pm.build_messages(
        task="Use file_read to inspect the source.",
        cwd=str(tmp_path),
        tools=tools,
        skills_manager=sm,
        injected_skills=[],
        history=[],
    )

    system_content = str(messages[0]["content"])
    assert "file_read" in system_content
    assert "shell_exec" not in system_content
    assert debug["tools_count"] == 1


def test_history_compacted_currently_uses_full_sliding_window_behavior(tmp_path: Path) -> None:
    sm, _skills = _make_skills_manager_with_two_skills(tmp_path)
    history = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "kept-a"},
        {"role": "user", "content": "kept-b"},
    ]

    def _build(mode: str):
        pm = PromptManager(
            templates=PromptTemplates(system_text="SYS", developer_text=""),
            include_skills_list=False,
            history_mode=mode,
            history_max_messages=2,
            history_max_chars=1_000,
        )
        return pm.build_messages(
            task="current task",
            cwd=str(tmp_path),
            tools=[],
            skills_manager=sm,
            injected_skills=[],
            history=history,
        )

    compacted_messages, compacted_debug = _build("compacted")
    full_messages, full_debug = _build("full")

    assert compacted_messages == full_messages
    assert compacted_debug["history_mode"] == "compacted"
    assert full_debug["history_mode"] == "full"
    assert compacted_debug["history_kept"] == full_debug["history_kept"] == 2
    assert compacted_debug["history_dropped"] == full_debug["history_dropped"] == 1


def test_skill_injection_none_and_render_none_skip_injected_skill(tmp_path: Path) -> None:
    sm, skills = _make_skills_manager_with_two_skills(tmp_path)
    mentioned = next(s for s in skills if s.skill_name == "mentioned_skill")

    for mode, render in (("none", "body"), ("explicit_only", "none")):
        pm = PromptManager(
            templates=PromptTemplates(system_text="SYS", developer_text=""),
            include_skills_list=False,
            skill_injection_mode=mode,
            skill_render=render,
        )
        messages, debug = pm.build_messages(
            task="Use $[demo:writing].mentioned_skill",
            cwd=str(tmp_path),
            tools=[],
            skills_manager=sm,
            injected_skills=[(mentioned, "mention", "$[demo:writing].mentioned_skill")],
            history=[],
        )

        joined = "\n".join(str(m["content"]) for m in messages)
        assert "<skill>" not in joined
        assert "mentioned desc" not in joined
        assert debug["injected_skills_count"] == 0


def test_skill_render_method_only_falls_back_to_summary_when_body_is_too_large(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root / "mentioned", name="mentioned_skill", description="mentioned desc")
    sm = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "injection": {"max_bytes": 4},
            "spaces": [{"id": "space-gen", "namespace": "demo:writing", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
        },
    )
    mentioned = next(s for s in sm.scan() if s.skill_name == "mentioned_skill")
    pm = PromptManager(
        templates=PromptTemplates(system_text="SYS", developer_text=""),
        include_skills_list=False,
        skill_injection_mode="explicit_only",
        skill_render="method_only",
    )

    messages, debug = pm.build_messages(
        task="Use $[demo:writing].mentioned_skill",
        cwd=str(tmp_path),
        tools=[],
        skills_manager=sm,
        injected_skills=[(mentioned, "mention", "$[demo:writing].mentioned_skill")],
        history=[],
    )

    joined = "\n".join(str(m["content"]) for m in messages)
    assert "Skill summary:" in joined
    assert "mentioned desc" in joined
    assert "<skill_method>" not in joined
    assert debug["injected_skills_count"] == 1


def test_skill_render_method_only_falls_back_to_summary_when_body_loader_fails(tmp_path: Path) -> None:
    sm, _skills = _make_skills_manager_with_two_skills(tmp_path)

    def _broken_body_loader() -> str:
        raise IOError("cannot read skill body")

    mentioned = Skill(
        space_id="space-gen",
        source_id="src-fs",
        namespace="demo:writing",
        skill_name="mentioned_skill",
        description="mentioned desc",
        locator="memory://mentioned",
        path=None,
        body_size=None,
        body_loader=_broken_body_loader,
        required_env_vars=[],
        metadata={},
    )
    pm = PromptManager(
        templates=PromptTemplates(system_text="SYS", developer_text=""),
        include_skills_list=False,
        skill_injection_mode="explicit_only",
        skill_render="method_only",
    )

    messages, debug = pm.build_messages(
        task="Use $[demo:writing].mentioned_skill",
        cwd=str(tmp_path),
        tools=[],
        skills_manager=sm,
        injected_skills=[(mentioned, "mention", "$[demo:writing].mentioned_skill")],
        history=[],
    )

    joined = "\n".join(str(m["content"]) for m in messages)
    assert "Skill summary:" in joined
    assert "mentioned desc" in joined
    assert "<skill_method>" not in joined
    assert "cannot read skill body" not in joined
    assert debug["injected_skills_count"] == 1
