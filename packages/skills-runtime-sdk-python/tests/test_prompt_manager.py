from __future__ import annotations

from pathlib import Path

from skills_runtime.prompts.history import trim_history
from skills_runtime.prompts.manager import PromptManager, PromptTemplates
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.tools.protocol import ToolSpec


def _write_skill(dir_path: Path, *, name: str, description: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "SKILL.md").write_text(
        "\n".join(["---", f"name: {name}", f'description: \"{description}\"', "---", "body", ""]),
        encoding="utf-8",
    )


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
