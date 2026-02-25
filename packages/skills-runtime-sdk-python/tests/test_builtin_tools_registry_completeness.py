from __future__ import annotations

from pathlib import Path

from skills_runtime.tools.builtin import register_builtin_tools
from skills_runtime.tools.registry import ToolExecutionContext, ToolRegistry


def test_register_builtin_tools_has_expected_codex_parity_set(tmp_path: Path) -> None:
    """
    回归护栏：builtin tools 的注册集合必须稳定，避免文档/CLI 漂移。

    说明：
    - 本断言覆盖当前 SDK 已交付范围（Codex parity，除 MCP）+ Phase 2/3/4 的既有 builtin tools。
    - 若未来新增/删除工具，需要先更新 specs（例如 tools.md/codex-capabilities-scope.md）再更新此测试。
    """

    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="r_tools")
    reg = ToolRegistry(ctx=ctx)
    register_builtin_tools(reg)

    got = {s.name for s in reg.list_specs()}
    expected = {
        # Phase 2
        "shell_exec",
        "file_read",
        "file_write",
        "ask_human",
        # Phase 3 (Skills 外延)
        "skill_exec",
        "skill_ref_read",
        # Phase 4 (Tools 标准库)
        "list_dir",
        "grep_files",
        "apply_patch",
        "read_file",
        # Phase 5 (Codex parity, 除 MCP)
        "shell",
        "shell_command",
        "exec_command",
        "write_stdin",
        "update_plan",
        "request_user_input",
        "view_image",
        "web_search",
        "spawn_agent",
        "wait",
        "send_input",
        "close_agent",
        "resume_agent",
    }

    assert got == expected

