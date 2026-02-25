"""
内置工具（builtin tools）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools.md` §4（builtin tools）

本包提供：
- Phase 2：`shell_exec/file_read/file_write/ask_human`
- Phase 3：`skill_exec/skill_ref_read`（Skills 外延能力）
- Phase 4：`list_dir/grep_files/apply_patch/read_file`
- Phase 5（Codex parity，除 MCP）：
  - Exec：`shell/shell_command/exec_command/write_stdin`
  - Workflow：`update_plan/request_user_input`
  - Web/Image：`view_image/web_search`
  - Collab：`spawn_agent/wait/send_input/close_agent/resume_agent`

以及 Phase 3 扩展（Skills 外延能力）：
- skill_exec
- skill_ref_read

以及 Phase 4 扩展（工具标准库）：
- list_dir
- grep_files
- apply_patch
- read_file
"""

from __future__ import annotations

from skills_runtime.tools.builtin.apply_patch import APPLY_PATCH_SPEC, apply_patch
from skills_runtime.tools.builtin.ask_human import ASK_HUMAN_SPEC, ask_human
from skills_runtime.tools.builtin.close_agent import CLOSE_AGENT_SPEC, close_agent
from skills_runtime.tools.builtin.exec_command import EXEC_COMMAND_SPEC, exec_command
from skills_runtime.tools.builtin.file_read import FILE_READ_SPEC, file_read
from skills_runtime.tools.builtin.file_write import FILE_WRITE_SPEC, file_write
from skills_runtime.tools.builtin.grep_files import GREP_FILES_SPEC, grep_files
from skills_runtime.tools.builtin.list_dir import LIST_DIR_SPEC, list_dir
from skills_runtime.tools.builtin.read_file import READ_FILE_SPEC, read_file
from skills_runtime.tools.builtin.request_user_input import REQUEST_USER_INPUT_SPEC, request_user_input
from skills_runtime.tools.builtin.resume_agent import RESUME_AGENT_SPEC, resume_agent
from skills_runtime.tools.builtin.send_input import SEND_INPUT_SPEC, send_input
from skills_runtime.tools.builtin.shell import SHELL_SPEC, shell
from skills_runtime.tools.builtin.shell_command import SHELL_COMMAND_SPEC, shell_command
from skills_runtime.tools.builtin.shell_exec import SHELL_EXEC_SPEC, shell_exec
from skills_runtime.tools.builtin.spawn_agent import SPAWN_AGENT_SPEC, spawn_agent
from skills_runtime.tools.builtin.skill_exec import SKILL_EXEC_SPEC, skill_exec
from skills_runtime.tools.builtin.skill_ref_read import SKILL_REF_READ_SPEC, skill_ref_read
from skills_runtime.tools.builtin.update_plan import UPDATE_PLAN_SPEC, update_plan
from skills_runtime.tools.builtin.view_image import VIEW_IMAGE_SPEC, view_image
from skills_runtime.tools.builtin.web_search import WEB_SEARCH_SPEC, web_search
from skills_runtime.tools.builtin.wait import WAIT_SPEC, wait_tool
from skills_runtime.tools.builtin.write_stdin import WRITE_STDIN_SPEC, write_stdin
from skills_runtime.tools.registry import ToolRegistry

__all__ = ["register_builtin_tools"]

_BUILTIN_TOOL_ENTRIES = [
    (SHELL_EXEC_SPEC, shell_exec),
    (SHELL_SPEC, shell),
    (SHELL_COMMAND_SPEC, shell_command),
    (EXEC_COMMAND_SPEC, exec_command),
    (WRITE_STDIN_SPEC, write_stdin),
    (FILE_READ_SPEC, file_read),
    (FILE_WRITE_SPEC, file_write),
    (ASK_HUMAN_SPEC, ask_human),
    (UPDATE_PLAN_SPEC, update_plan),
    (REQUEST_USER_INPUT_SPEC, request_user_input),
    (VIEW_IMAGE_SPEC, view_image),
    (WEB_SEARCH_SPEC, web_search),
    (SPAWN_AGENT_SPEC, spawn_agent),
    (WAIT_SPEC, wait_tool),
    (SEND_INPUT_SPEC, send_input),
    (CLOSE_AGENT_SPEC, close_agent),
    (RESUME_AGENT_SPEC, resume_agent),
    (SKILL_EXEC_SPEC, skill_exec),
    (SKILL_REF_READ_SPEC, skill_ref_read),
    (LIST_DIR_SPEC, list_dir),
    (GREP_FILES_SPEC, grep_files),
    (APPLY_PATCH_SPEC, apply_patch),
    (READ_FILE_SPEC, read_file),
]


def register_builtin_tools(registry: ToolRegistry, *, override: bool = False) -> None:
    """
    注册 builtin tools 集合（按当前 SDK 已启用范围）。

    参数：
    - registry：工具注册表
    - override：是否允许覆盖同名工具（默认 False）
    """

    for spec, handler in _BUILTIN_TOOL_ENTRIES:
        registry.register(spec, handler, override=override)
