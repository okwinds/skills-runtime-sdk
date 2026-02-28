from __future__ import annotations

import hashlib

from skills_runtime.safety.descriptors import (
    ApplyPatchDescriptor,
    ExecCommandDescriptor,
    FileWriteDescriptor,
    ShellCommandDescriptor,
    ShellDescriptor,
    ShellExecDescriptor,
    SkillExecDescriptor,
    WriteStdinDescriptor,
)
from skills_runtime.tools.protocol import ToolSafetyDescriptor


def test_shell_exec_policy_category_is_shell() -> None:
    descriptor = ShellExecDescriptor()
    assert descriptor.policy_category == "shell"


def test_shell_exec_extract_risk_high_for_rm_rf_root() -> None:
    descriptor = ShellExecDescriptor()
    risk = descriptor.extract_risk({"argv": ["rm", "-rf", "/"]})
    assert risk["risk_level"] == "high"


def test_shell_exec_extract_risk_low_for_ls() -> None:
    descriptor = ShellExecDescriptor()
    risk = descriptor.extract_risk({"argv": ["ls", "-la"]})
    assert risk["risk_level"] == "low"


def test_shell_exec_sanitize_for_approval_hides_env_values() -> None:
    descriptor = ShellExecDescriptor()
    req = descriptor.sanitize_for_approval({"argv": ["ls"], "env": {"SECRET": "val"}})
    assert req["env_keys"] == ["SECRET"]
    assert "env" not in req


def test_shell_exec_sanitize_for_event_hides_env_values() -> None:
    descriptor = ShellExecDescriptor()
    req = descriptor.sanitize_for_event({"argv": ["ls"], "env": {"SECRET": "val"}})
    assert req["env_keys"] == ["SECRET"]
    assert "env" not in req


def test_shell_descriptor_extract_risk_maps_command_to_argv() -> None:
    descriptor = ShellDescriptor()
    risk = descriptor.extract_risk({"command": ["echo", "hi"]})
    assert risk["argv"] == ["echo", "hi"]


def test_shell_descriptor_sanitize_uses_workdir_as_cwd() -> None:
    descriptor = ShellDescriptor()
    req = descriptor.sanitize_for_approval({"command": ["echo", "hi"], "workdir": "/tmp"})
    assert req["cwd"] == "/tmp"


def test_shell_descriptor_policy_category_is_shell() -> None:
    descriptor = ShellDescriptor()
    assert descriptor.policy_category == "shell"


def test_shell_command_policy_category_is_shell() -> None:
    descriptor = ShellCommandDescriptor()
    assert descriptor.policy_category == "shell"


def test_shell_command_extract_risk_parses_argv() -> None:
    descriptor = ShellCommandDescriptor()
    risk = descriptor.extract_risk({"command": "ls -la"})
    assert risk["argv"] == ["ls", "-la"]


def test_shell_command_extract_risk_marks_complex_command_as_high() -> None:
    descriptor = ShellCommandDescriptor()
    risk = descriptor.extract_risk({"command": "echo hi && rm -rf /"})
    assert risk["is_complex"] is True
    assert risk["risk_level"] == "high"


def test_shell_command_sanitize_hides_env_values() -> None:
    descriptor = ShellCommandDescriptor()
    req = descriptor.sanitize_for_approval({"command": "echo ok", "env": {"SECRET": "val"}})
    assert req["env_keys"] == ["SECRET"]
    assert "env" not in req


def test_exec_command_extract_risk_parses_cmd_to_argv() -> None:
    descriptor = ExecCommandDescriptor()
    risk = descriptor.extract_risk({"cmd": "ls -la"})
    assert risk["argv"] == ["ls", "-la"]


def test_exec_command_policy_category_is_shell() -> None:
    descriptor = ExecCommandDescriptor()
    assert descriptor.policy_category == "shell"


def test_exec_command_sanitize_includes_default_tty_true() -> None:
    descriptor = ExecCommandDescriptor()
    req = descriptor.sanitize_for_approval({"cmd": "ls -la"})
    assert req["tty"] is True


def test_file_write_policy_category_is_file() -> None:
    descriptor = FileWriteDescriptor()
    assert descriptor.policy_category == "file"


def test_file_write_sanitize_for_approval_uses_digest_without_content() -> None:
    descriptor = FileWriteDescriptor()
    req = descriptor.sanitize_for_approval({"path": "a.txt", "content": "hello"})
    assert req["bytes"] == 5
    assert req["content_sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert "content" not in req


def test_file_write_extract_risk_returns_low_with_empty_argv() -> None:
    descriptor = FileWriteDescriptor()
    risk = descriptor.extract_risk({"path": "a.txt", "content": "hello"})
    assert risk["argv"] == []
    assert risk["risk_level"] == "low"


def test_apply_patch_policy_category_is_file() -> None:
    descriptor = ApplyPatchDescriptor()
    assert descriptor.policy_category == "file"


def test_apply_patch_sanitize_extracts_file_paths() -> None:
    descriptor = ApplyPatchDescriptor()
    req = descriptor.sanitize_for_approval({"input": "*** Add File: foo.py\n+print(1)\n"})
    assert req["file_paths"] == ["foo.py"]


def test_apply_patch_sanitize_does_not_include_input() -> None:
    descriptor = ApplyPatchDescriptor()
    req = descriptor.sanitize_for_approval({"input": "*** Add File: foo.py\n+print(1)\n"})
    assert "input" not in req


def test_skill_exec_policy_category_is_shell() -> None:
    descriptor = SkillExecDescriptor()
    assert descriptor.policy_category == "shell"


def test_skill_exec_sanitize_includes_mention_and_action() -> None:
    descriptor = SkillExecDescriptor()
    req = descriptor.sanitize_for_approval({"skill_mention": "$[ns].skill", "action_id": "run"})
    assert req["skill_mention"] == "$[ns].skill"
    assert req["action_id"] == "run"


def test_skill_exec_extract_risk_without_resolution_defaults_non_shell_low_visibility() -> None:
    descriptor = SkillExecDescriptor()
    risk = descriptor.extract_risk({"skill_mention": "$[ns].skill", "action_id": "run"})
    assert "risk_level" in risk


def test_write_stdin_policy_category_is_file() -> None:
    descriptor = WriteStdinDescriptor()
    assert descriptor.policy_category == "file"


def test_write_stdin_sanitize_for_approval_uses_digest_without_chars() -> None:
    descriptor = WriteStdinDescriptor()
    req = descriptor.sanitize_for_approval({"session_id": 1, "chars": "hello"})
    assert req["session_id"] == 1
    assert req["bytes"] == 5
    assert req["chars_sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert "chars" not in req


def test_write_stdin_sanitize_for_event_uses_digest_without_chars() -> None:
    descriptor = WriteStdinDescriptor()
    req = descriptor.sanitize_for_event({"session_id": 1, "chars": "hello"})
    assert req["bytes"] == 5
    assert req["chars_sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert "chars" not in req


def test_all_descriptors_are_tool_safety_descriptor_protocol() -> None:
    descriptors = [
        ShellExecDescriptor(),
        ShellDescriptor(),
        ShellCommandDescriptor(),
        ExecCommandDescriptor(),
        FileWriteDescriptor(),
        ApplyPatchDescriptor(),
        SkillExecDescriptor(),
        WriteStdinDescriptor(),
    ]
    assert all(isinstance(d, ToolSafetyDescriptor) for d in descriptors)
