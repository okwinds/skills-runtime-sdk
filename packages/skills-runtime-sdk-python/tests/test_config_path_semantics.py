from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import pytest

from skills_runtime.core.agent import Agent
from skills_runtime.config.defaults import load_default_config_dict
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.tools.protocol import ToolSpec


class _CaptureBackend:
    def __init__(self) -> None:
        self.last_messages: Optional[List[Dict[str, Any]]] = None

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        self.last_messages = request.messages
        yield ChatStreamEvent(type="text_delta", text="ok")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def _write_skill(root: Path, *, name: str = "demo", description: str = "d") -> Path:
    skill_dir = root / name
    (skill_dir / "agents").mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "---",
                "",
                "# Body",
                "content",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return skill_md


def test_default_config_dict_has_expected_shape() -> None:
    d = load_default_config_dict()
    assert isinstance(d, dict)
    assert int(d.get("config_version") or 0) >= 1
    assert isinstance(d.get("llm"), dict)
    assert isinstance(d.get("models"), dict)


def test_prompt_system_path_relative_resolves_under_workspace_root(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    other = tmp_path / "other"
    (ws / "prompts").mkdir(parents=True, exist_ok=True)
    other.mkdir(parents=True, exist_ok=True)

    (ws / "prompts" / "sys.md").write_text("SYSFILE", encoding="utf-8")
    overlay = ws / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "prompt:",
                "  system_path: prompts/sys.md",
                "  include_skills_list: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(other)
    backend = _CaptureBackend()
    agent = Agent(backend=backend, workspace_root=ws, config_paths=[overlay])
    agent.run("hi")

    assert backend.last_messages is not None
    assert backend.last_messages[0]["role"] == "system"
    assert "SYSFILE" in str(backend.last_messages[0]["content"])


def test_prompt_developer_path_relative_resolves_under_workspace_root(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    other = tmp_path / "other"
    (ws / "prompts").mkdir(parents=True, exist_ok=True)
    other.mkdir(parents=True, exist_ok=True)

    (ws / "prompts" / "dev.md").write_text("DEVFILE", encoding="utf-8")
    overlay = ws / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "prompt:",
                "  developer_path: prompts/dev.md",
                "  include_skills_list: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(other)
    backend = _CaptureBackend()
    agent = Agent(backend=backend, workspace_root=ws, config_paths=[overlay])
    agent.run("hi")

    assert backend.last_messages is not None
    sys = str(backend.last_messages[0]["content"])
    assert "DEVFILE" in sys


def test_prompt_paths_support_absolute_paths(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "prompts").mkdir(parents=True, exist_ok=True)

    sys_path = (ws / "prompts" / "sys.md").resolve()
    sys_path.write_text("ABS_SYS", encoding="utf-8")
    overlay = ws / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "prompt:",
                f"  system_path: {sys_path}",
                "  include_skills_list: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    backend = _CaptureBackend()
    agent = Agent(backend=backend, workspace_root=ws, config_paths=[overlay])
    agent.run("hi")
    assert backend.last_messages is not None
    assert "ABS_SYS" in str(backend.last_messages[0]["content"])


def test_skills_roots_relative_resolves_under_workspace_root(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """legacy skills.roots 必须在配置加载阶段 fail-fast。"""

    ws = tmp_path / "ws"
    other = tmp_path / "other"
    (ws / "skills").mkdir(parents=True, exist_ok=True)
    other.mkdir(parents=True, exist_ok=True)

    _write_skill(ws / "skills", name="demo-skill", description="desc")
    overlay = ws / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "skills:",
                "  roots: [skills]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(other)
    backend = _CaptureBackend()
    with pytest.raises(Exception):
        Agent(backend=backend, workspace_root=ws, config_paths=[overlay])


def test_explicit_skills_roots_relative_is_resolved_under_workspace_root(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """legacy skills_roots 构造参数已移除。"""

    ws = tmp_path / "ws"
    other = tmp_path / "other"
    (ws / "skills").mkdir(parents=True, exist_ok=True)
    other.mkdir(parents=True, exist_ok=True)

    _write_skill(ws / "skills", name="s1", description="d1")
    monkeypatch.chdir(other)

    backend = _CaptureBackend()
    with pytest.raises(TypeError):
        Agent(backend=backend, workspace_root=ws, skills_roots=[Path("skills")])  # type: ignore[call-arg]


def test_skills_disabled_paths_relative_resolves_under_workspace_root(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    other = tmp_path / "other"
    (ws / "skills").mkdir(parents=True, exist_ok=True)
    other.mkdir(parents=True, exist_ok=True)

    skill_md = _write_skill(ws / "skills", name="s2", description="d2")
    overlay = ws / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "skills:",
                "  spaces:",
                "    - id: \"space-demo\"",
                "      namespace: \"demo:local\"",
                "      sources: [\"src-fs\"]",
                "  sources:",
                "    - id: \"src-fs\"",
                "      type: \"filesystem\"",
                "      options:",
                "        root: skills",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(other)
    backend = _CaptureBackend()
    agent = Agent(
        backend=backend,
        workspace_root=ws,
        config_paths=[overlay],
        skills_disabled_paths=[Path("skills") / "s2" / "SKILL.md"],
    )
    agent.run("hi")

    assert backend.last_messages is not None
    joined = "\n".join(str(m.get("content") or "") for m in backend.last_messages)
    assert "$[demo:local].s2" not in joined


def test_filesystem_source_root_relative_resolves_under_workspace_root(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    other = tmp_path / "other"
    (ws / "skills").mkdir(parents=True, exist_ok=True)
    other.mkdir(parents=True, exist_ok=True)

    _write_skill(ws / "skills", name="demo-skill", description="desc")
    overlay = ws / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "skills:",
                "  spaces:",
                "    - id: \"space-demo\"",
                "      namespace: \"demo:local\"",
                "      sources: [\"src-fs\"]",
                "  sources:",
                "    - id: \"src-fs\"",
                "      type: \"filesystem\"",
                "      options:",
                "        root: skills",
                "prompt:",
                "  include_skills_list: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(other)
    backend = _CaptureBackend()
    agent = Agent(backend=backend, workspace_root=ws, config_paths=[overlay])
    agent.run("hi")

    assert backend.last_messages is not None
    joined = "\n".join(str(m.get("content") or "") for m in backend.last_messages)
    assert "$[demo:local].demo-skill" in joined
    assert "desc" in joined


@pytest.mark.parametrize(
    "rel_path",
    [
        "prompts/sys.md",
        "./prompts/sys.md",
    ],
)
def test_prompt_path_variants_are_resolved_under_workspace_root(tmp_path: Path, monkeypatch, rel_path: str) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    other = tmp_path / "other"
    (ws / "prompts").mkdir(parents=True, exist_ok=True)
    other.mkdir(parents=True, exist_ok=True)

    (ws / "prompts" / "sys.md").write_text("SYSX", encoding="utf-8")
    overlay = ws / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "prompt:",
                f"  system_path: {rel_path}",
                "  include_skills_list: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(other)
    backend = _CaptureBackend()
    agent = Agent(backend=backend, workspace_root=ws, config_paths=[overlay])
    agent.run("hi")
    assert backend.last_messages is not None
    assert "SYSX" in str(backend.last_messages[0]["content"])
