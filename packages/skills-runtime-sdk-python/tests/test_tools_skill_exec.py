from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

import pytest

from skills_runtime.skills.manager import SkillsManager
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


class _FakeCommandResult:
    def __init__(
        self,
        *,
        ok: bool,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = 0,
        error_kind: str | None = None,
        truncated: bool = False,
    ) -> None:
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration_ms = 1
        self.truncated = truncated
        self.error_kind = error_kind


class _RecordingExecutor:
    """记录 run_command 调用的 executor（用于断言 argv/cwd/env/timeout）。"""

    def __init__(self, *, result: Optional[_FakeCommandResult] = None) -> None:
        self.calls: list[dict] = []
        self._result = result or _FakeCommandResult(ok=True, stdout="ok")

    def run_command(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: Optional[Mapping[str, str]] = None,
        timeout_ms: int = 60_000,
        cancel_checker=None,
    ):
        self.calls.append({"argv": list(argv), "cwd": Path(cwd), "env": dict(env or {}), "timeout_ms": int(timeout_ms)})
        return self._result


def _write_skill_bundle(
    bundle_root: Path,
    *,
    name: str = "python_testing",
    description: str = "d",
    extra_frontmatter_lines: list[str] | None = None,
    body: str = "body\n",
) -> Path:
    """写入最小 filesystem skill bundle，并返回 `SKILL.md` 路径。"""

    bundle_root.mkdir(parents=True, exist_ok=True)
    skill_md = bundle_root / "SKILL.md"
    fm = ["---", f"name: {name}", f'description: \"{description}\"']
    fm.extend(list(extra_frontmatter_lines or []))
    fm.append("---")
    skill_md.write_text("\n".join([*fm, body.rstrip("\n"), ""]), encoding="utf-8")
    return skill_md


def _mk_manager(*, workspace_root: Path, skills_root: Path, skills_config_extra: dict | None = None) -> SkillsManager:
    """创建一个仅包含 filesystem source 的 SkillsManager。"""

    cfg: dict = {
        "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
        "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
    }
    if skills_config_extra:
        cfg.update(skills_config_extra)
    mgr = SkillsManager(workspace_root=workspace_root, skills_config=cfg)
    mgr.scan()
    return mgr


def _mk_ctx(*, workspace_root: Path, skills_manager: SkillsManager, executor: _RecordingExecutor) -> ToolExecutionContext:
    """构造 ToolExecutionContext（skill_exec 需要 executor，以复用 shell_exec）。"""

    return ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="run_test",
        wal=None,
        executor=executor,
        human_io=None,
        env={},
        cancel_checker=None,
        redaction_values=[],
        default_timeout_ms=123,
        max_file_bytes=10,
        sandbox_policy_default="none",
        sandbox_adapter=None,
        emit_tool_events=False,
        event_sink=None,
        skills_manager=skills_manager,
    )


def _call_exec(*, action_id: str, skill_mention: str = "$[alice:engineering].python_testing") -> ToolCall:
    """构造 skill_exec ToolCall。"""

    return ToolCall(call_id="c1", name="skill_exec", args={"skill_mention": skill_mention, "action_id": action_id})


def _assert_framework_error_payload(result, *, code: str) -> None:  # type: ignore[no-untyped-def]
    """断言 ToolResult 以框架错误结构返回（英文 code/message/details）。"""

    assert result.ok is False
    assert isinstance(result.details, dict)
    data = (result.details or {}).get("data") or {}
    assert isinstance(data, dict)
    err = data.get("error") or {}
    assert isinstance(err, dict)
    assert err.get("code") == code
    assert isinstance(err.get("message"), str) and err.get("message")
    assert isinstance(err.get("details"), dict)


def test_skill_exec_disabled_returns_permission(tmp_path: Path) -> None:
    """actions 默认关闭：必须直接失败（permission），不执行任何命令。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(
        bundle,
        extra_frontmatter_lines=[
            "actions:",
            "  run_tests:",
            "    kind: shell",
            "    argv: [\"bash\", \"actions/run_tests.sh\"]",
        ],
    )
    (bundle / "actions").mkdir(parents=True, exist_ok=True)
    (bundle / "actions" / "run_tests.sh").write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"actions": {"enabled": False}})
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    result = skill_exec(_call_exec(action_id="run_tests"), ctx)
    assert result.ok is False
    assert result.error_kind == "permission"
    _assert_framework_error_payload(result, code="SKILL_ACTIONS_DISABLED")
    assert ex.calls == []


def test_skill_exec_invalid_mention_returns_validation(tmp_path: Path) -> None:
    """skill_mention 非法必须失败（validation）。"""

    skills_root = tmp_path / "skills_root"
    _write_skill_bundle(skills_root / "python_testing")
    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"actions": {"enabled": True}})
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    result = skill_exec(_call_exec(action_id="x", skill_mention="$[bad"), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_MENTION_FORMAT_INVALID")
    assert ex.calls == []


def test_skill_exec_unknown_skill_returns_not_found(tmp_path: Path) -> None:
    """skill 不存在必须失败（not_found）。"""

    skills_root = tmp_path / "skills_root"
    _write_skill_bundle(skills_root / "python_testing")
    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"actions": {"enabled": True}})
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    result = skill_exec(_call_exec(action_id="x", skill_mention="$[alice:engineering].does_not_exist"), ctx)
    assert result.ok is False
    assert result.error_kind == "not_found"
    _assert_framework_error_payload(result, code="SKILL_UNKNOWN")
    assert ex.calls == []


def test_skill_exec_rejects_unknown_args(tmp_path: Path) -> None:
    """skill_exec args 出现未知字段必须失败（validation）。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(
        bundle,
        extra_frontmatter_lines=[
            "actions:",
            "  run_tests:",
            "    kind: shell",
            "    argv: [\"bash\", \"actions/run_tests.sh\"]",
        ],
    )
    (bundle / "actions").mkdir(parents=True, exist_ok=True)
    (bundle / "actions" / "run_tests.sh").write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"actions": {"enabled": True}})
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    call = ToolCall(
        call_id="c1",
        name="skill_exec",
        args={"skill_mention": "$[alice:engineering].python_testing", "action_id": "run_tests", "x": "1"},
    )
    result = skill_exec(call, ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_ACTION_DEFINITION_INVALID")
    assert ex.calls == []


def test_skill_exec_source_unsupported_for_in_memory(tmp_path: Path) -> None:
    """非 filesystem source（in-memory）必须失败（validation）。"""

    mgr = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-mem"]}],
            "sources": [{"id": "src-mem", "type": "in-memory", "options": {"namespace": "ns"}}],
            "actions": {"enabled": True},
        },
        in_memory_registry={
            "ns": [
                {
                    "skill_name": "python_testing",
                    "description": "d",
                    "body": "body",
                    "metadata": {"actions": {"run_tests": {"kind": "shell", "argv": ["echo", "hi"]}}},
                }
            ]
        },
    )
    mgr.scan()
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    result = skill_exec(_call_exec(action_id="run_tests"), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_ACTION_SOURCE_UNSUPPORTED")
    assert ex.calls == []


def test_skill_exec_action_not_found(tmp_path: Path) -> None:
    """action_id 不存在必须失败（not_found）。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(bundle, extra_frontmatter_lines=["actions: {}", ""])
    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"actions": {"enabled": True}})
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    result = skill_exec(_call_exec(action_id="does_not_exist"), ctx)
    assert result.ok is False
    assert result.error_kind == "not_found"
    _assert_framework_error_payload(result, code="SKILL_ACTION_NOT_FOUND")
    assert ex.calls == []


def test_skill_exec_action_argv_invalid(tmp_path: Path) -> None:
    """argv 非法必须失败（validation）。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(
        bundle,
        extra_frontmatter_lines=[
            "actions:",
            "  run_tests:",
            "    kind: shell",
            "    argv: \"not-a-list\"",
        ],
    )
    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"actions": {"enabled": True}})
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    result = skill_exec(_call_exec(action_id="run_tests"), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_ACTION_DEFINITION_INVALID")
    assert ex.calls == []


@pytest.mark.parametrize(
    "argv",
    [
        ["bash", "../evil.sh"],
        ["bash", "/etc/passwd"],
        ["bash", "actions/../evil.sh"],
    ],
)
def test_skill_exec_action_path_escape_is_rejected(tmp_path: Path, argv: list[str]) -> None:
    """action argv 逃逸 bundle_root 必须拒绝（permission）。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(
        bundle,
        extra_frontmatter_lines=[
            "actions:",
            "  run_tests:",
            "    kind: shell",
            f"    argv: {argv!r}",
        ],
    )
    (bundle / "actions").mkdir(parents=True, exist_ok=True)
    (bundle / "actions" / "run_tests.sh").write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"actions": {"enabled": True}})
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    result = skill_exec(_call_exec(action_id="run_tests"), ctx)
    assert result.ok is False
    assert result.error_kind == "permission"
    _assert_framework_error_payload(result, code="SKILL_ACTION_ARGV_PATH_ESCAPE")
    assert ex.calls == []


def test_skill_exec_executes_via_shell_exec_and_records_env(tmp_path: Path) -> None:
    """action 正常时必须通过 shell_exec 执行，并注入稳定 env（bundle/workspace/mention）。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(
        bundle,
        extra_frontmatter_lines=[
            "actions:",
            "  run_tests:",
            "    kind: shell",
            "    argv: [\"bash\", \"actions/run_tests.sh\"]",
            "    timeout_ms: 900000",
            "    env: {\"X\": \"1\"}",
        ],
    )
    (bundle / "actions").mkdir(parents=True, exist_ok=True)
    (bundle / "actions" / "run_tests.sh").write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"actions": {"enabled": True}})
    ex = _RecordingExecutor(result=_FakeCommandResult(ok=True, stdout="hello"))
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    result = skill_exec(_call_exec(action_id="run_tests"), ctx)
    assert result.ok is True
    assert result.details and result.details["stdout"] == "hello"

    assert ex.calls, "executor.run_command should be called"
    call0 = ex.calls[0]
    assert call0["argv"][0] == "bash"
    assert call0["argv"][1].endswith("actions/run_tests.sh")
    assert call0["timeout_ms"] == 900000

    env = call0["env"]
    assert env.get("X") == "1"
    assert env.get("SKILLS_RUNTIME_SDK_WORKSPACE_ROOT") == str(tmp_path.resolve())
    assert env.get("SKILLS_RUNTIME_SDK_SKILL_BUNDLE_ROOT") == str(bundle.resolve())
    assert env.get("SKILLS_RUNTIME_SDK_SKILL_MENTION") == "$[alice:engineering].python_testing"
    assert env.get("SKILLS_RUNTIME_SDK_SKILL_ACTION_ID") == "run_tests"
    assert set(env.keys()) == {
        "X",
        "SKILLS_RUNTIME_SDK_WORKSPACE_ROOT",
        "SKILLS_RUNTIME_SDK_SKILL_BUNDLE_ROOT",
        "SKILLS_RUNTIME_SDK_SKILL_MENTION",
        "SKILLS_RUNTIME_SDK_SKILL_ACTION_ID",
    }


def test_skill_exec_action_missing_script_is_validation(tmp_path: Path) -> None:
    """argv 指向 bundle 内脚本但文件不存在：必须失败（validation）。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(
        bundle,
        extra_frontmatter_lines=[
            "actions:",
            "  run_tests:",
            "    kind: shell",
            "    argv: [\"bash\", \"actions/missing.sh\"]",
        ],
    )
    (bundle / "actions").mkdir(parents=True, exist_ok=True)

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"actions": {"enabled": True}})
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr, executor=ex)

    from skills_runtime.tools.builtin.skill_exec import skill_exec

    result = skill_exec(_call_exec(action_id="run_tests"), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_ACTION_ARGV_PATH_INVALID")
    assert ex.calls == []
