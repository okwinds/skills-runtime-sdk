from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Optional

from skills_runtime.core.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.tools.protocol import ToolCall


class _Backend:
    """最小 fake backend：先触发一次 tool_call，再正常结束，避免 agent 进入循环。"""

    def __init__(self, tool_call: ToolCall) -> None:
        self._call = tool_call
        self._count = 0

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:  # type: ignore[override]
        _ = request
        if self._count == 0:
            self._count += 1
            yield ChatStreamEvent(type="tool_calls", tool_calls=[self._call], finish_reason="tool_calls")
            yield ChatStreamEvent(type="completed", finish_reason="tool_calls")
            return
        yield ChatStreamEvent(type="text_delta", text="done")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def _event_text(events: list[Any]) -> str:
    return "\n".join(e.to_json() for e in events)


def _write_skill_bundle(bundle_root: Path) -> None:
    """写入最小 filesystem skill bundle（包含 actions 定义，供 skill_exec 解析）。"""

    bundle_root.mkdir(parents=True, exist_ok=True)
    skill_md = bundle_root / "SKILL.md"
    secret_value = "SKILL_ENV_SECRET_SHOULD_NOT_LEAK"
    skill_md.write_text(
        "\n".join(
            [
                "---",
                "name: python_testing",
                'description: "d"',
                "actions:",
                "  run_tests:",
                "    kind: shell",
                '    argv: ["bash", "actions/run_tests.sh"]',
                "    env:",
                f"      OPENAI_API_KEY: {secret_value}",
                "---",
                "body",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _mk_manager(*, workspace_root: Path, skills_root: Path) -> SkillsManager:
    """创建一个仅包含 filesystem source 的 SkillsManager。"""

    cfg: dict = {
        "spaces": [{"id": "space-eng", "namespace": "alice:engineering", "sources": ["src-fs"]}],
        "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
        "actions": {"enabled": True},
    }
    mgr = SkillsManager(workspace_root=workspace_root, skills_config=cfg)
    mgr.scan()
    return mgr


def _write_safety_overlay(*, tmp_path: Path, mode: str) -> Path:
    """写入最小 overlay：用于让本次 run 在不执行工具的情况下产出事件。"""

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "safety:",
                f"  mode: {mode}",
                "  allowlist: []",
                "  denylist: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return overlay


def test_tool_call_requested_for_skill_exec_includes_intent_argv(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(bundle)
    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root)

    overlay = _write_safety_overlay(tmp_path=tmp_path, mode="deny")

    call = ToolCall(
        call_id="c1",
        name="skill_exec",
        args={"skill_mention": "$[alice:engineering].python_testing", "action_id": "run_tests"},
        raw_arguments=None,
    )
    agent = Agent(
        backend=_Backend(call),
        workspace_root=tmp_path,
        skills_manager=mgr,
        approval_provider=None,
        config_paths=[overlay],
    )
    events = list(agent.run_stream("run"))

    requested = next(e for e in events if e.type == "tool_call_requested")
    args = requested.payload.get("arguments") or {}
    assert isinstance(args, dict)

    # 关键断言：事件侧的脱敏表示需要能解析出 intent argv（与 approvals request 口径一致）
    assert args.get("argv") == ["bash", "actions/run_tests.sh"]
    assert args.get("env_keys") == ["OPENAI_API_KEY"]
    assert isinstance(args.get("bundle_root"), str) and str(args.get("bundle_root")).endswith("python_testing")

    # secrets 不得出现在任何事件 JSON 中（即使它存在于 skill frontmatter 里）
    assert "SKILL_ENV_SECRET_SHOULD_NOT_LEAK" not in _event_text(events)


def test_tool_call_requested_for_skill_exec_without_skills_manager_degrades_safely(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    # 不注入 skills_manager：事件侧无法解析 action→argv，但必须保持结构可序列化且不泄密
    overlay = _write_safety_overlay(tmp_path=tmp_path, mode="deny")
    call = ToolCall(
        call_id="c1",
        name="skill_exec",
        args={"skill_mention": "$[alice:engineering].python_testing", "action_id": "run_tests"},
        raw_arguments=None,
    )
    agent = Agent(
        backend=_Backend(call),
        workspace_root=tmp_path,
        skills_manager=None,
        approval_provider=None,
        config_paths=[overlay],
    )
    events = list(agent.run_stream("run"))

    requested = next(e for e in events if e.type == "tool_call_requested")
    args = requested.payload.get("arguments") or {}
    assert isinstance(args, dict)
    assert args.get("skill_mention") == "$[alice:engineering].python_testing"
    assert args.get("action_id") == "run_tests"

    # best-effort：此时 argv 可能为空，但结构必须稳定可审计
    assert "argv" in args
    assert isinstance(args.get("argv"), list)

