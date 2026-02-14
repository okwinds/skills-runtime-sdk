"""
Skills Runtime SDK CLI（skills/tools/runs）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/skills-cli.md`
- `docs/specs/skills-runtime-sdk/docs/tools-cli.md`

约束：
- 使用 argparse（不引入第三方 CLI 依赖）
- stdout 输出机器可读 JSON；尽量在失败时也输出 JSON
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from pydantic import ValidationError

from agent_sdk import bootstrap
from agent_sdk.config.defaults import load_default_config_dict
from agent_sdk.config.loader import AgentSdkConfig, load_config_dicts
from agent_sdk.core.errors import FrameworkError, FrameworkIssue
from agent_sdk.core.executor import Executor
from agent_sdk.core.exec_sessions import PersistentExecSessionManager
from agent_sdk.core.collab_persistent import PersistentCollabManager
from agent_sdk.skills.manager import SkillsManager
from agent_sdk.skills.models import ScanReport, _json_sanitize
from agent_sdk.observability.run_metrics import compute_run_metrics_summary
from agent_sdk.tools.builtin import register_builtin_tools
from agent_sdk.tools.protocol import ToolCall, ToolResult
from agent_sdk.tools.registry import ToolExecutionContext, ToolRegistry
from agent_sdk.core.utf8 import ensure_utf8_stdio


def _dump_json_to_stdout(obj: Dict[str, Any], *, pretty: bool) -> None:
    """
    将 dict 输出为 JSON 到 stdout（末尾包含换行）。

    参数：
    - obj：待输出对象（必须可 JSON dumps；调用方需确保已做清洗）
    - pretty：是否启用 pretty-print（indent=2）
    """

    if pretty:
        text = json.dumps(obj, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    print(text)


def _resolve_workspace_root(raw: str) -> Tuple[Optional[Path], Optional[FrameworkIssue]]:
    """
    解析 workspace_root 参数为绝对路径。

    返回：
    - (workspace_root, issue)：当解析失败时返回 (None, issue)；成功时 issue 为 None。
    """

    try:
        ws = Path(raw).expanduser().resolve()
    except Exception as exc:
        return None, FrameworkIssue(
            code="CLI_WORKSPACE_ROOT_INVALID",
            message="Workspace root is invalid.",
            details={"workspace_root": raw, "reason": str(exc)},
        )
    if not ws.exists() or not ws.is_dir():
        return None, FrameworkIssue(
            code="CLI_WORKSPACE_ROOT_NOT_FOUND",
            message="Workspace root is not found or not a directory.",
            details={"workspace_root": str(ws)},
        )
    return ws, None


def _resolve_overlay_path(workspace_root: Path, raw: str) -> Path:
    """将 overlay 路径解析为绝对路径（相对路径相对 workspace_root）。"""

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (workspace_root / p).resolve()
    return p.resolve()


def _load_yaml_mapping(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[FrameworkIssue]]:
    """
    加载 YAML overlay 并确保根节点为 mapping(dict)。

    返回：
    - (mapping, issue)：失败时 mapping 为 None，issue 为错误信息（英文结构化）。
    """

    if not path.exists():
        return None, FrameworkIssue(
            code="CLI_OVERLAY_NOT_FOUND",
            message="Overlay config not found.",
            details={"path": str(path)},
        )
    try:
        obj = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return None, FrameworkIssue(
            code="CLI_OVERLAY_LOAD_FAILED",
            message="Overlay config load failed.",
            details={"path": str(path), "reason": str(exc)},
        )
    if not isinstance(obj, dict):
        return None, FrameworkIssue(
            code="CLI_OVERLAY_INVALID",
            message="Overlay config root must be an object.",
            details={"path": str(path), "actual": type(obj).__name__},
        )
    return obj, None


def _load_effective_config(
    *,
    workspace_root: Path,
    overlay_paths: List[Path],
) -> Tuple[Optional[AgentSdkConfig], List[FrameworkIssue]]:
    """
    加载默认配置 + overlays，返回校验后的 AgentSdkConfig（fail-open）。

    返回：
    - (config, issues)：当加载失败时 config 为 None，issues 至少包含一条 error。
    """

    overlays: List[Dict[str, Any]] = [load_default_config_dict()]
    issues: List[FrameworkIssue] = []

    for p in overlay_paths:
        obj, issue = _load_yaml_mapping(p)
        if issue is not None:
            issues.append(issue)
            continue
        overlays.append(obj or {})

    if issues:
        return None, issues

    try:
        return load_config_dicts(overlays), []
    except ValidationError as exc:
        return None, [
            FrameworkIssue(
                code="CLI_CONFIG_INVALID",
                message="Config is invalid.",
                details={"reason": str(exc)},
            )
        ]
    except Exception as exc:
        return None, [
            FrameworkIssue(
                code="CLI_CONFIG_LOAD_FAILED",
                message="Config load failed.",
                details={"reason": str(exc)},
            )
        ]


def _issues_to_jsonable(issues: List[FrameworkIssue]) -> List[Dict[str, Any]]:
    """将 FrameworkIssue 列表投影为可 JSON 序列化结构（details 做清洗）。"""

    out: List[Dict[str, Any]] = []
    for it in issues:
        details = it.details if isinstance(it.details, dict) else {"value": it.details}
        out.append({"code": it.code, "message": it.message, "details": _json_sanitize(details)})
    return out


def _count_issue_levels(issues: List[FrameworkIssue]) -> Tuple[int, int]:
    """
    统计 issues 中 error/warning 的数量（基于 details.level 约定）。

    返回：
    - (errors_total, warnings_total)
    """

    warnings_total = 0
    for it in issues:
        if isinstance(it.details, dict) and it.details.get("level") == "warning":
            warnings_total += 1
    errors_total = max(0, len(issues) - warnings_total)
    return errors_total, warnings_total


def _exit_code_for_preflight(issues: List[FrameworkIssue]) -> int:
    """按 spec 计算 preflight exit code（0/10/12）。"""

    errors_total, warnings_total = _count_issue_levels(issues)
    if errors_total > 0:
        return 10
    if warnings_total > 0:
        return 12
    return 0


def _exit_code_for_scan(report: ScanReport) -> int:
    """按 spec 计算 scan exit code（0/11/12）。"""

    if report.errors:
        return 11
    if report.warnings:
        return 12
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI argparse parser。"""

    parser = argparse.ArgumentParser(
        prog="skills-runtime-sdk",
        description="Skills Runtime SDK CLI（skills/tools/runs）。",
    )
    root_sub = parser.add_subparsers(dest="command", required=True)

    def _add_common_flags(p: argparse.ArgumentParser) -> None:
        """为子命令添加公共 flags。"""

        p.add_argument("--workspace-root", default=".", help="Workspace root directory (default: .)")
        p.add_argument("--config", action="append", default=[], help="Overlay config YAML path (repeatable).")
        p.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
        p.add_argument("--no-dotenv", action="store_true", help="Disable loading .env from workspace root.")

    skills = root_sub.add_parser("skills", help="Skills commands")
    skills_sub = skills.add_subparsers(dest="skills_cmd", required=True)

    preflight = skills_sub.add_parser("preflight", aliases=["validate-config"], help="Preflight skills config")
    _add_common_flags(preflight)

    scan = skills_sub.add_parser("scan", help="Scan skills (metadata-only)")
    _add_common_flags(scan)

    tools = root_sub.add_parser("tools", help="Builtin tools commands")
    tools_sub = tools.add_subparsers(dest="tools_cmd", required=True)

    list_dir = tools_sub.add_parser("list-dir", help="Call builtin tool: list_dir")
    _add_common_flags(list_dir)
    list_dir.add_argument("--dir-path", required=True, help="Directory path to list (must be within workspace).")
    list_dir.add_argument("--depth", type=int, default=2, help="Recursion depth (>=1).")
    list_dir.add_argument("--offset", type=int, default=1, help="1-indexed offset (>=1).")
    list_dir.add_argument("--limit", type=int, default=25, help="Max entries to return (>=1).")

    grep_files = tools_sub.add_parser("grep-files", help="Call builtin tool: grep_files")
    _add_common_flags(grep_files)
    grep_files.add_argument("--pattern", required=True, help="Regex/search pattern (non-empty).")
    grep_files.add_argument("--path", default=None, help="Search root (default: workspace root).")
    grep_files.add_argument("--include", default=None, help="Glob include filter (e.g. '*.md').")
    grep_files.add_argument("--limit", type=int, default=100, help="Max matched files to return (>=1).")

    apply_patch = tools_sub.add_parser("apply-patch", help="Call builtin tool: apply_patch")
    _add_common_flags(apply_patch)
    group = apply_patch.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", default=None, help="Patch text (Codex apply_patch format).")
    group.add_argument("--input-file", default=None, help="Patch file path (utf-8).")
    apply_patch.add_argument("--yes", action="store_true", help="Required to execute write operation.")

    read_file = tools_sub.add_parser("read-file", help="Call builtin tool: read_file (slice/indentation)")
    _add_common_flags(read_file)
    read_file.add_argument("--file-path", required=True, help="File path to read (must be within workspace).")
    read_file.add_argument("--offset", type=int, default=1, help="1-indexed line offset (>=1).")
    read_file.add_argument("--limit", type=int, default=2000, help="Max lines to return (>=1).")
    read_file.add_argument("--mode", default="slice", help="Mode (slice|indentation).")
    read_file.add_argument("--anchor-line", type=int, default=None, help="Indentation: anchor line (>=1).")
    read_file.add_argument("--max-levels", type=int, default=4, help="Indentation: max levels (>=0; 0=unlimited).")
    read_file.add_argument("--include-siblings", action="store_true", help="Indentation: include siblings.")
    read_file.add_argument("--include-header", action="store_true", help="Indentation: include header.")
    read_file.add_argument("--max-lines", type=int, default=None, help="Indentation: max output lines (>=1).")

    shell_p = tools_sub.add_parser("shell", help="Call builtin tool: shell (argv)")
    _add_common_flags(shell_p)
    shell_p.add_argument("--yes", action="store_true", help="Required to execute exec operation.")
    shell_p.add_argument("--workdir", default=None, help="Working directory (relative to workspace root).")
    shell_p.add_argument("--timeout-ms", type=int, default=None, help="Timeout in ms (>=1).")
    shell_p.add_argument("--sandbox", default=None, help="Sandbox policy (inherit|none|restricted).")
    shell_p.add_argument("--sandbox-permissions", default=None, help="Sandbox permissions (restricted|require-escalated).")
    shell_p.add_argument("argv", nargs=argparse.REMAINDER, help="Command argv; use `--` before argv.")

    shell_cmd_p = tools_sub.add_parser("shell-command", help="Call builtin tool: shell_command (string)")
    _add_common_flags(shell_cmd_p)
    shell_cmd_p.add_argument("--yes", action="store_true", help="Required to execute exec operation.")
    shell_cmd_p.add_argument("--command", dest="command_text", required=True, help="Command string to execute.")
    shell_cmd_p.add_argument("--workdir", default=None, help="Working directory (relative to workspace root).")
    shell_cmd_p.add_argument("--timeout-ms", type=int, default=None, help="Timeout in ms (>=1).")
    shell_cmd_p.add_argument("--sandbox", default=None, help="Sandbox policy (inherit|none|restricted).")
    shell_cmd_p.add_argument("--sandbox-permissions", default=None, help="Sandbox permissions (restricted|require-escalated).")

    exec_cmd_p = tools_sub.add_parser("exec-command", help="Call builtin tool: exec_command (PTY session)")
    _add_common_flags(exec_cmd_p)
    exec_cmd_p.add_argument("--yes", action="store_true", help="Required to execute exec operation.")
    exec_cmd_p.add_argument("--cmd", required=True, help="Command string to execute.")
    exec_cmd_p.add_argument("--workdir", default=None, help="Working directory (relative to workspace root).")
    exec_cmd_p.add_argument("--yield-time-ms", type=int, default=50, help="Yield time in ms (>=0).")
    exec_cmd_p.add_argument("--max-output-tokens", type=int, default=None, help="Max output tokens (>=1).")
    exec_cmd_p.add_argument("--tty", action="store_true", help="Allocate TTY (default: true).")
    exec_cmd_p.add_argument("--sandbox", default=None, help="Sandbox policy (inherit|none|restricted).")
    exec_cmd_p.add_argument("--sandbox-permissions", default=None, help="Sandbox permissions (restricted|require-escalated).")

    write_stdin_p = tools_sub.add_parser("write-stdin", help="Call builtin tool: write_stdin (PTY session)")
    _add_common_flags(write_stdin_p)
    write_stdin_p.add_argument("--yes", action="store_true", help="Required to execute exec operation.")
    write_stdin_p.add_argument("--session-id", type=int, required=True, help="Session id (>=1).")
    write_stdin_p.add_argument("--chars", default=None, help="Chars to write (optional).")
    write_stdin_p.add_argument("--yield-time-ms", type=int, default=50, help="Yield time in ms (>=0).")
    write_stdin_p.add_argument("--max-output-tokens", type=int, default=None, help="Max output tokens (>=1).")

    update_plan_p = tools_sub.add_parser("update-plan", help="Call builtin tool: update_plan")
    _add_common_flags(update_plan_p)
    group3 = update_plan_p.add_mutually_exclusive_group(required=True)
    group3.add_argument("--input", default=None, help="JSON text for update_plan args.")
    group3.add_argument("--input-file", default=None, help="JSON file path (utf-8) for update_plan args.")

    req_input_p = tools_sub.add_parser("request-user-input", help="Call builtin tool: request_user_input")
    _add_common_flags(req_input_p)
    group4 = req_input_p.add_mutually_exclusive_group(required=True)
    group4.add_argument("--input", default=None, help="JSON text for request_user_input args.")
    group4.add_argument("--input-file", default=None, help="JSON file path (utf-8) for request_user_input args.")
    req_input_p.add_argument("--answers-json", default=None, help="Optional JSON object mapping question_id -> answer.")

    view_image_p = tools_sub.add_parser("view-image", help="Call builtin tool: view_image")
    _add_common_flags(view_image_p)
    view_image_p.add_argument("--path", required=True, help="Image path (must be within workspace).")

    web_search_p = tools_sub.add_parser("web-search", help="Call builtin tool: web_search (disabled by default)")
    _add_common_flags(web_search_p)
    web_search_p.add_argument("--q", required=True, help="Search query.")
    web_search_p.add_argument("--limit", type=int, default=10, help="Max results (>=1).")
    web_search_p.add_argument("--recency", type=int, default=None, help="Recency in days (>=0).")

    spawn_p = tools_sub.add_parser("spawn-agent", help="Call builtin tool: spawn_agent")
    _add_common_flags(spawn_p)
    spawn_p.add_argument("--yes", action="store_true", help="Required to execute exec operation.")
    spawn_p.add_argument("--message", required=True, help="Initial message for child agent.")
    spawn_p.add_argument("--agent-type", default=None, help="Child agent type (optional).")

    wait_p = tools_sub.add_parser("wait", help="Call builtin tool: wait")
    _add_common_flags(wait_p)
    wait_p.add_argument("--ids", required=True, help="Comma-separated child agent ids.")
    wait_p.add_argument("--timeout-ms", type=int, default=None, help="Timeout in ms (>=1).")

    send_p = tools_sub.add_parser("send-input", help="Call builtin tool: send_input")
    _add_common_flags(send_p)
    send_p.add_argument("--yes", action="store_true", help="Required to execute exec operation.")
    send_p.add_argument("--id", required=True, help="Child agent id.")
    send_p.add_argument("--message", required=True, help="Message to send.")
    send_p.add_argument("--interrupt", action="store_true", help="Interrupt (optional).")

    close_p = tools_sub.add_parser("close-agent", help="Call builtin tool: close_agent")
    _add_common_flags(close_p)
    close_p.add_argument("--yes", action="store_true", help="Required to execute exec operation.")
    close_p.add_argument("--id", required=True, help="Child agent id.")

    resume_p = tools_sub.add_parser("resume-agent", help="Call builtin tool: resume_agent")
    _add_common_flags(resume_p)
    resume_p.add_argument("--yes", action="store_true", help="Required to execute exec operation.")
    resume_p.add_argument("--id", required=True, help="Child agent id.")

    runs = root_sub.add_parser("runs", help="Run-related commands")
    runs_sub = runs.add_subparsers(dest="runs_cmd", required=True)

    metrics = runs_sub.add_parser("metrics", help="Compute run metrics summary from events.jsonl")
    metrics.add_argument("--workspace-root", default=".", help="Workspace root directory (default: .)")
    group2 = metrics.add_mutually_exclusive_group(required=True)
    group2.add_argument("--run-id", default=None, help="Run id under workspace_root/.skills_runtime_sdk/runs/<run_id>/events.jsonl")
    group2.add_argument("--events-path", default=None, help="Explicit events.jsonl path (relative to workspace root if not absolute).")
    metrics.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")

    return parser


def _exit_code_for_tool_result(result: ToolResult) -> int:
    """
    将 ToolResult 映射为 tools CLI exit code。

    约定：
    - ok=true -> 0
    - validation -> 20
    - permission -> 21
    - not_found -> 22
    - sandbox_denied -> 24
    - timeout -> 25
    - human_required -> 26
    - cancelled -> 27
    - 其它/未知 -> 23
    """

    if bool(result.ok):
        return 0

    kind = str(result.error_kind or "")
    if kind == "human_required":
        return 26
    if kind == "validation":
        return 20
    if kind == "permission":
        return 21
    if kind == "not_found":
        return 22
    if kind == "sandbox_denied":
        return 24
    if kind == "timeout":
        return 25
    if kind == "cancelled":
        return 27
    return 23


def _tool_result_to_jsonable(result: ToolResult) -> Dict[str, Any]:
    """将 ToolResult 投影为稳定 JSON（优先使用 details；兜底解析 content）。"""

    if isinstance(result.details, dict):
        return result.details
    try:
        obj = json.loads(result.content)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {
        "ok": bool(result.ok),
        "stdout": "",
        "stderr": str(result.content or ""),
        "duration_ms": 0,
        "truncated": False,
        "error_kind": result.error_kind or "unknown",
        "retryable": False,
    }


def _prepare_bootstrap_for_cli(args: argparse.Namespace) -> Tuple[Optional[Path], List[Path], Optional[Path], Optional[str]]:
    """
    复用 skills CLI 的 bootstrap 语义，返回：
    - workspace_root（绝对路径；失败则 None）
    - overlay_paths（绝对路径列表；保序）
    - env_file（实际加载的 env 文件；若无则 None）
    - dotenv_error（若加载失败则为错误字符串；成功/未启用则 None）
    """

    ws, ws_issue = _resolve_workspace_root(str(args.workspace_root))
    if ws_issue is not None:
        return None, [], None, ws_issue.message

    overlay_paths: List[Path] = []
    env_file: Optional[Path] = None
    dotenv_error: Optional[str] = None

    if ws is None:
        return None, [], None, "workspace_root is invalid"

    if not bool(args.no_dotenv):
        try:
            env_file = bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)
        except Exception as exc:
            dotenv_error = str(exc)

    overlay_paths.extend(bootstrap.discover_overlay_paths(workspace_root=ws))
    for raw in list(getattr(args, "config", []) or []):
        overlay_paths.append(_resolve_overlay_path(ws, str(raw)))

    return ws, overlay_paths, env_file, dotenv_error


def _dispatch_builtin_tool(
    *,
    workspace_root: Path,
    tool_name: str,
    tool_args: Dict[str, Any],
    human_io: Any = None,
    web_search_provider: Any = None,
) -> ToolResult:
    """构造 ToolRegistry 并派发执行 builtin tool。"""

    exec_tools = {"exec_command", "write_stdin"}
    collab_tools = {"spawn_agent", "wait", "send_input", "close_agent", "resume_agent"}

    exec_sessions = None
    if tool_name in exec_tools:
        exec_sessions = PersistentExecSessionManager(workspace_root=workspace_root)

    collab_manager = None
    if tool_name in collab_tools:
        collab_manager = PersistentCollabManager(workspace_root=workspace_root)

    ctx = ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="tools_cli",
        wal=None,
        executor=Executor(),
        human_io=human_io,
        env=None,
        exec_sessions=exec_sessions,
        web_search_provider=web_search_provider,
        collab_manager=collab_manager,
        emit_tool_events=False,
    )
    registry = ToolRegistry(ctx=ctx)
    register_builtin_tools(registry)

    call = ToolCall(call_id=f"cli_{tool_name}_{uuid.uuid4().hex}", name=tool_name, args=tool_args)
    return registry.dispatch(call)


def _dump_tools_cli_payload(
    *,
    tool_name: str,
    result: ToolResult,
    workspace_root: Path,
    overlay_paths: List[Path],
    env_file: Optional[Path],
    dotenv_error: Optional[str],
    pretty: bool,
) -> None:
    """输出 tools CLI 统一 JSON envelope（tool/result/stats）。"""

    stats: Dict[str, Any] = {
        "workspace_root": str(workspace_root),
        "overlay_paths": [str(p) for p in overlay_paths],
        "env_file": str(env_file) if env_file is not None else None,
    }
    if dotenv_error:
        stats["dotenv_error"] = dotenv_error

    payload = {"tool": tool_name, "result": _tool_result_to_jsonable(result), "stats": stats}
    _dump_json_to_stdout(payload, pretty=pretty)


def _require_yes_or_human_required(tool_name: str) -> ToolResult:
    """缺少 CLI `--yes` 时返回 human_required（exit code=26）。"""

    return ToolResult.error_payload(
        error_kind="human_required",
        stderr="--yes is required for this operation",
        data={"tool": tool_name},
    )


def _handle_tools_list_dir(args: argparse.Namespace) -> int:
    """执行 `tools list-dir`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="list_dir",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    tool_args: Dict[str, Any] = {
        "dir_path": str(args.dir_path),
        "depth": int(args.depth),
        "offset": int(args.offset),
        "limit": int(args.limit),
    }
    result = _dispatch_builtin_tool(workspace_root=ws, tool_name="list_dir", tool_args=tool_args)
    _dump_tools_cli_payload(
        tool_name="list_dir",
        result=result,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result)


def _handle_tools_grep_files(args: argparse.Namespace) -> int:
    """执行 `tools grep-files`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="grep_files",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    tool_args2: Dict[str, Any] = {"pattern": str(args.pattern)}
    if args.path is not None:
        tool_args2["path"] = str(args.path)
    if args.include is not None:
        tool_args2["include"] = str(args.include)
    tool_args2["limit"] = int(args.limit)

    result = _dispatch_builtin_tool(workspace_root=ws, tool_name="grep_files", tool_args=tool_args2)
    _dump_tools_cli_payload(
        tool_name="grep_files",
        result=result,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result)


def _resolve_input_file_path(*, workspace_root: Path, raw: str) -> Tuple[Optional[Path], Optional[ToolResult]]:
    """
    解析 `--input-file` 为绝对路径并做 workspace_root 边界校验。

    返回：
    - (path, error_result)：成功时 error_result 为 None；失败时 path 为 None。
    """

    try:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (workspace_root / p).resolve()
        p = p.resolve()
    except Exception as exc:
        return None, ToolResult.error_payload(error_kind="validation", stderr=str(exc))

    try:
        # 复用 ToolExecutionContext 的边界语义
        ctx = ToolExecutionContext(workspace_root=workspace_root, run_id="tools_cli", emit_tool_events=False)
        _ = ctx.resolve_path(str(p))
    except Exception as exc:
        return None, ToolResult.error_payload(error_kind="permission", stderr=str(exc))

    if not p.exists():
        return None, ToolResult.error_payload(error_kind="not_found", stderr=f"input file not found: {p}")
    if not p.is_file():
        return None, ToolResult.error_payload(error_kind="validation", stderr="input file must be a file")
    return p, None


def _load_json_object_from_input(
    *,
    workspace_root: Path,
    input_text: Optional[str],
    input_file: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[ToolResult]]:
    """
    从 --input/--input-file 加载 JSON object（dict）。

    返回：
    - (obj, err)：成功时 err=None；失败时 obj=None 且 err 为 ToolResult（validation/permission/not_found）。
    """

    raw: Optional[str] = None
    if input_text is not None:
        raw = str(input_text)
    elif input_file is not None:
        p, err = _resolve_input_file_path(workspace_root=workspace_root, raw=str(input_file))
        if err is not None:
            return None, err
        assert p is not None
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception as exc:
            return None, ToolResult.error_payload(error_kind="validation", stderr=str(exc))
    else:
        return None, ToolResult.error_payload(error_kind="validation", stderr="missing input")

    try:
        obj = json.loads(raw or "")
    except Exception as exc:
        return None, ToolResult.error_payload(error_kind="validation", stderr=str(exc))
    if not isinstance(obj, dict):
        return None, ToolResult.error_payload(error_kind="validation", stderr="input JSON root must be an object")
    return obj, None


def _handle_tools_apply_patch(args: argparse.Namespace) -> int:
    """执行 `tools apply-patch`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="apply_patch",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    if not bool(args.yes):
        result = ToolResult.error_payload(
            error_kind="human_required",
            stderr="apply_patch is a write operation; require explicit --yes",
            data={"requires_approval": True},
        )
        _dump_tools_cli_payload(
            tool_name="apply_patch",
            result=result,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    patch_text: Optional[str] = None
    if args.input is not None:
        patch_text = str(args.input)
    if patch_text is None and args.input_file is not None:
        p, err = _resolve_input_file_path(workspace_root=ws, raw=str(args.input_file))
        if err is not None:
            _dump_tools_cli_payload(
                tool_name="apply_patch",
                result=err,
                workspace_root=ws,
                overlay_paths=overlays,
                env_file=env_file,
                dotenv_error=dotenv_error,
                pretty=bool(args.pretty),
            )
            return _exit_code_for_tool_result(err)
        assert p is not None
        patch_text = p.read_text(encoding="utf-8")

    if patch_text is None:
        # argparse 的 mutually-exclusive-group(required=True) 理论上已覆盖，这里只是兜底
        result2 = ToolResult.error_payload(error_kind="validation", stderr="must provide --input or --input-file")
        _dump_tools_cli_payload(
            tool_name="apply_patch",
            result=result2,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result2)

    result = _dispatch_builtin_tool(workspace_root=ws, tool_name="apply_patch", tool_args={"input": patch_text})
    _dump_tools_cli_payload(
        tool_name="apply_patch",
        result=result,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result)


def _handle_tools_read_file(args: argparse.Namespace) -> int:
    """执行 `tools read-file`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="read_file",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    tool_args: Dict[str, Any] = {
        "file_path": str(args.file_path),
        "offset": int(args.offset),
        "limit": int(args.limit),
        "mode": str(args.mode),
    }
    if str(args.mode).strip().lower() == "indentation":
        indentation: Dict[str, Any] = {
            "max_levels": int(args.max_levels),
            "include_siblings": bool(args.include_siblings),
            "include_header": bool(args.include_header),
        }
        if args.anchor_line is not None:
            indentation["anchor_line"] = int(args.anchor_line)
        if args.max_lines is not None:
            indentation["max_lines"] = int(args.max_lines)
        tool_args["indentation"] = indentation
    result2 = _dispatch_builtin_tool(workspace_root=ws, tool_name="read_file", tool_args=tool_args)
    _dump_tools_cli_payload(
        tool_name="read_file",
        result=result2,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result2)


def _handle_tools_shell(args: argparse.Namespace) -> int:
    """执行 `tools shell`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="shell",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    if not bool(args.yes):
        result2 = _require_yes_or_human_required("shell")
        _dump_tools_cli_payload(
            tool_name="shell",
            result=result2,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result2)

    argv = list(getattr(args, "argv", []) or [])
    if argv and argv[0] == "--":
        argv = argv[1:]
    tool_args: Dict[str, Any] = {"command": argv}
    if args.workdir is not None:
        tool_args["workdir"] = str(args.workdir)
    if args.timeout_ms is not None:
        tool_args["timeout_ms"] = int(args.timeout_ms)
    if args.sandbox is not None:
        tool_args["sandbox"] = str(args.sandbox)
    if args.sandbox_permissions is not None:
        tool_args["sandbox_permissions"] = str(args.sandbox_permissions)

    result3 = _dispatch_builtin_tool(workspace_root=ws, tool_name="shell", tool_args=tool_args)
    _dump_tools_cli_payload(
        tool_name="shell",
        result=result3,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result3)


def _handle_tools_shell_command(args: argparse.Namespace) -> int:
    """执行 `tools shell-command`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="shell_command",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    if not bool(args.yes):
        result2 = _require_yes_or_human_required("shell_command")
        _dump_tools_cli_payload(
            tool_name="shell_command",
            result=result2,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result2)

    tool_args: Dict[str, Any] = {"command": str(args.command_text)}
    if args.workdir is not None:
        tool_args["workdir"] = str(args.workdir)
    if args.timeout_ms is not None:
        tool_args["timeout_ms"] = int(args.timeout_ms)
    if args.sandbox is not None:
        tool_args["sandbox"] = str(args.sandbox)
    if args.sandbox_permissions is not None:
        tool_args["sandbox_permissions"] = str(args.sandbox_permissions)

    result3 = _dispatch_builtin_tool(workspace_root=ws, tool_name="shell_command", tool_args=tool_args)
    _dump_tools_cli_payload(
        tool_name="shell_command",
        result=result3,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result3)


def _handle_tools_exec_command(args: argparse.Namespace) -> int:
    """执行 `tools exec-command`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="exec_command",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    if not bool(args.yes):
        result2 = _require_yes_or_human_required("exec_command")
        _dump_tools_cli_payload(
            tool_name="exec_command",
            result=result2,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result2)

    tool_args: Dict[str, Any] = {"cmd": str(args.cmd), "yield_time_ms": int(args.yield_time_ms)}
    if args.workdir is not None:
        tool_args["workdir"] = str(args.workdir)
    if args.max_output_tokens is not None:
        tool_args["max_output_tokens"] = int(args.max_output_tokens)
    if bool(args.tty):
        tool_args["tty"] = True
    if args.sandbox is not None:
        tool_args["sandbox"] = str(args.sandbox)
    if args.sandbox_permissions is not None:
        tool_args["sandbox_permissions"] = str(args.sandbox_permissions)

    result3 = _dispatch_builtin_tool(workspace_root=ws, tool_name="exec_command", tool_args=tool_args)
    _dump_tools_cli_payload(
        tool_name="exec_command",
        result=result3,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result3)


def _handle_tools_write_stdin(args: argparse.Namespace) -> int:
    """执行 `tools write-stdin`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="write_stdin",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    if not bool(args.yes):
        result2 = _require_yes_or_human_required("write_stdin")
        _dump_tools_cli_payload(
            tool_name="write_stdin",
            result=result2,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result2)

    tool_args: Dict[str, Any] = {"session_id": int(args.session_id), "yield_time_ms": int(args.yield_time_ms)}
    if args.chars is not None:
        tool_args["chars"] = str(args.chars)
    if args.max_output_tokens is not None:
        tool_args["max_output_tokens"] = int(args.max_output_tokens)

    result3 = _dispatch_builtin_tool(workspace_root=ws, tool_name="write_stdin", tool_args=tool_args)
    _dump_tools_cli_payload(
        tool_name="write_stdin",
        result=result3,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result3)


def _handle_tools_update_plan(args: argparse.Namespace) -> int:
    """执行 `tools update-plan`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="update_plan",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    obj, err = _load_json_object_from_input(workspace_root=ws, input_text=args.input, input_file=args.input_file)
    if err is not None:
        _dump_tools_cli_payload(
            tool_name="update_plan",
            result=err,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(err)
    assert obj is not None

    result2 = _dispatch_builtin_tool(workspace_root=ws, tool_name="update_plan", tool_args=obj)
    _dump_tools_cli_payload(
        tool_name="update_plan",
        result=result2,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result2)


def _handle_tools_request_user_input(args: argparse.Namespace) -> int:
    """执行 `tools request-user-input`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="request_user_input",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    obj, err = _load_json_object_from_input(workspace_root=ws, input_text=args.input, input_file=args.input_file)
    if err is not None:
        _dump_tools_cli_payload(
            tool_name="request_user_input",
            result=err,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(err)
    assert obj is not None

    human_io = None
    if args.answers_json is not None:
        try:
            answers_obj = json.loads(str(args.answers_json))
        except Exception as exc:
            result_bad = ToolResult.error_payload(error_kind="validation", stderr=str(exc))
            _dump_tools_cli_payload(
                tool_name="request_user_input",
                result=result_bad,
                workspace_root=ws,
                overlay_paths=overlays,
                env_file=env_file,
                dotenv_error=dotenv_error,
                pretty=bool(args.pretty),
            )
            return _exit_code_for_tool_result(result_bad)
        if not isinstance(answers_obj, dict):
            result_bad2 = ToolResult.error_payload(error_kind="validation", stderr="answers_json must be a JSON object")
            _dump_tools_cli_payload(
                tool_name="request_user_input",
                result=result_bad2,
                workspace_root=ws,
                overlay_paths=overlays,
                env_file=env_file,
                dotenv_error=dotenv_error,
                pretty=bool(args.pretty),
            )
            return _exit_code_for_tool_result(result_bad2)

        class _CliHumanIO:
            """
            CLI 用的 HumanIOProvider 适配器（离线答案模式）。

            语义：
            - 从 `--answers-json` 提供的 mapping 中按 `question_id` 取答案；
            - 当 mapping 缺失时：回退为第一个 choice（若存在），否则返回空串。
            """

            def __init__(self, mapping: Dict[str, Any]) -> None:
                """
                创建离线答案 provider。

                参数：
                - mapping：`question_id -> answer` 的映射（任意 JSON 值会被转为字符串）
                """

                self._mapping = dict(mapping)

            def request_human_input(  # type: ignore[no-untyped-def]
                self, *, call_id: str, question: str, choices, context, timeout_ms
            ) -> str:
                """
                返回某个问题的离线答案（HumanIOProvider 协议）。

                参数：
                - call_id：框架生成的 call id（约定以 `...:<question_id>` 结尾）
                - question/choices/context/timeout_ms：框架侧透传信息（本实现仅用于兜底选择）

                返回：
                - str：答案文本
                """

                qid = str(call_id).split(":")[-1]
                if qid in self._mapping:
                    return str(self._mapping[qid])
                # 未提供则回退为第一个 choice（若存在），否则空串
                if choices:
                    return str(list(choices)[0])
                return ""

        human_io = _CliHumanIO(answers_obj)

    result2 = _dispatch_builtin_tool(workspace_root=ws, tool_name="request_user_input", tool_args=obj, human_io=human_io)
    _dump_tools_cli_payload(
        tool_name="request_user_input",
        result=result2,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result2)


def _handle_tools_view_image(args: argparse.Namespace) -> int:
    """执行 `tools view-image`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="view_image",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    result2 = _dispatch_builtin_tool(workspace_root=ws, tool_name="view_image", tool_args={"path": str(args.path)})
    _dump_tools_cli_payload(
        tool_name="view_image",
        result=result2,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result2)


def _handle_tools_web_search(args: argparse.Namespace) -> int:
    """执行 `tools web-search`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="web_search",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    tool_args: Dict[str, Any] = {"q": str(args.q), "limit": int(args.limit)}
    if args.recency is not None:
        tool_args["recency"] = int(args.recency)
    # 默认 disabled：不注入 provider（保持 fail-closed）；产品侧可通过 wrapper 注入 provider
    result2 = _dispatch_builtin_tool(workspace_root=ws, tool_name="web_search", tool_args=tool_args, web_search_provider=None)
    _dump_tools_cli_payload(
        tool_name="web_search",
        result=result2,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result2)


def _handle_tools_spawn_agent(args: argparse.Namespace) -> int:
    """执行 `tools spawn-agent`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="spawn_agent",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    if not bool(args.yes):
        result2 = _require_yes_or_human_required("spawn_agent")
        _dump_tools_cli_payload(
            tool_name="spawn_agent",
            result=result2,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result2)

    tool_args: Dict[str, Any] = {"message": str(args.message)}
    if args.agent_type is not None:
        tool_args["agent_type"] = str(args.agent_type)
    result3 = _dispatch_builtin_tool(workspace_root=ws, tool_name="spawn_agent", tool_args=tool_args)
    _dump_tools_cli_payload(
        tool_name="spawn_agent",
        result=result3,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result3)


def _handle_tools_wait(args: argparse.Namespace) -> int:
    """执行 `tools wait`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="wait",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    ids = [s for s in str(args.ids).split(",") if s.strip()]
    tool_args: Dict[str, Any] = {"ids": ids}
    if args.timeout_ms is not None:
        tool_args["timeout_ms"] = int(args.timeout_ms)
    result2 = _dispatch_builtin_tool(workspace_root=ws, tool_name="wait", tool_args=tool_args)
    _dump_tools_cli_payload(
        tool_name="wait",
        result=result2,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result2)


def _handle_tools_send_input(args: argparse.Namespace) -> int:
    """执行 `tools send-input`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="send_input",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    if not bool(args.yes):
        result2 = _require_yes_or_human_required("send_input")
        _dump_tools_cli_payload(
            tool_name="send_input",
            result=result2,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result2)

    tool_args: Dict[str, Any] = {"id": str(args.id), "message": str(args.message)}
    if bool(args.interrupt):
        tool_args["interrupt"] = True
    result3 = _dispatch_builtin_tool(workspace_root=ws, tool_name="send_input", tool_args=tool_args)
    _dump_tools_cli_payload(
        tool_name="send_input",
        result=result3,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result3)


def _handle_tools_close_agent(args: argparse.Namespace) -> int:
    """执行 `tools close-agent`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="close_agent",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    if not bool(args.yes):
        result2 = _require_yes_or_human_required("close_agent")
        _dump_tools_cli_payload(
            tool_name="close_agent",
            result=result2,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result2)

    result3 = _dispatch_builtin_tool(workspace_root=ws, tool_name="close_agent", tool_args={"id": str(args.id)})
    _dump_tools_cli_payload(
        tool_name="close_agent",
        result=result3,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result3)


def _handle_tools_resume_agent(args: argparse.Namespace) -> int:
    """执行 `tools resume-agent`。"""

    ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
    if ws is None:
        result = ToolResult.error_payload(error_kind="validation", stderr="workspace_root is invalid")
        _dump_tools_cli_payload(
            tool_name="resume_agent",
            result=result,
            workspace_root=Path(str(args.workspace_root)).expanduser().resolve(),
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result)

    if not bool(args.yes):
        result2 = _require_yes_or_human_required("resume_agent")
        _dump_tools_cli_payload(
            tool_name="resume_agent",
            result=result2,
            workspace_root=ws,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(args.pretty),
        )
        return _exit_code_for_tool_result(result2)

    result3 = _dispatch_builtin_tool(workspace_root=ws, tool_name="resume_agent", tool_args={"id": str(args.id)})
    _dump_tools_cli_payload(
        tool_name="resume_agent",
        result=result3,
        workspace_root=ws,
        overlay_paths=overlays,
        env_file=env_file,
        dotenv_error=dotenv_error,
        pretty=bool(args.pretty),
    )
    return _exit_code_for_tool_result(result3)


def _handle_preflight(args: argparse.Namespace) -> int:
    """执行 `skills preflight` 并输出 JSON。"""

    ws, ws_issue = _resolve_workspace_root(str(args.workspace_root))
    overlay_paths: List[Path] = []
    env_file: Optional[Path] = None

    issues: List[FrameworkIssue] = []
    if ws_issue is not None:
        issues.append(ws_issue)
    if ws is not None:
        if not bool(args.no_dotenv):
            try:
                env_file = bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)
            except Exception as exc:
                issues.append(
                    FrameworkIssue(
                        code="CLI_DOTENV_LOAD_FAILED",
                        message="Dotenv load failed.",
                        details={"workspace_root": str(ws), "reason": str(exc)},
                    )
                )

        overlay_paths.extend(bootstrap.discover_overlay_paths(workspace_root=ws))
        for raw in list(args.config or []):
            overlay_paths.append(_resolve_overlay_path(ws, str(raw)))

    overlay_paths_str = [str(p) for p in overlay_paths]

    if ws is not None and not issues:
        config, load_issues = _load_effective_config(workspace_root=ws, overlay_paths=overlay_paths)
        if load_issues:
            issues.extend(load_issues)
        elif config is None:
            issues.append(
                FrameworkIssue(
                    code="CLI_CONFIG_LOAD_FAILED",
                    message="Config load failed.",
                    details={},
                )
            )
        else:
            mgr = SkillsManager(workspace_root=ws, skills_config=config.skills)
            issues.extend(mgr.preflight())

    exit_code = _exit_code_for_preflight(issues)
    errors_total, warnings_total = _count_issue_levels(issues)
    payload = {
        "issues": _issues_to_jsonable(issues),
        "stats": {
            "workspace_root": str(ws) if ws is not None else str(args.workspace_root),
            "overlay_paths": overlay_paths_str,
            "env_file": str(env_file) if env_file is not None else None,
            "issues_total": len(issues),
            "errors_total": errors_total,
            "warnings_total": warnings_total,
        },
    }
    _dump_json_to_stdout(payload, pretty=bool(args.pretty))
    return exit_code


def _handle_scan(args: argparse.Namespace) -> int:
    """执行 `skills scan` 并输出 ScanReport JSON。"""

    ws, ws_issue = _resolve_workspace_root(str(args.workspace_root))
    overlay_paths: List[Path] = []
    issues: List[FrameworkIssue] = []

    if ws_issue is not None:
        issues.append(ws_issue)

    if ws is not None:
        if not bool(args.no_dotenv):
            try:
                bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)
            except Exception as exc:
                issues.append(
                    FrameworkIssue(
                        code="CLI_DOTENV_LOAD_FAILED",
                        message="Dotenv load failed.",
                        details={"workspace_root": str(ws), "reason": str(exc)},
                    )
                )

        overlay_paths.extend(bootstrap.discover_overlay_paths(workspace_root=ws))
        for raw in list(args.config or []):
            overlay_paths.append(_resolve_overlay_path(ws, str(raw)))

    report: ScanReport
    if ws is None or issues:
        report = ScanReport(
            scan_id="scan_cli_error",
            skills=[],
            errors=issues,
            warnings=[],
            stats={"spaces_total": 0, "sources_total": 0, "skills_total": 0},
        )
        _dump_json_to_stdout(report.to_jsonable(), pretty=bool(args.pretty))
        return _exit_code_for_scan(report)

    config, load_issues = _load_effective_config(workspace_root=ws, overlay_paths=overlay_paths)
    if load_issues or config is None:
        report = ScanReport(
            scan_id="scan_cli_error",
            skills=[],
            errors=load_issues or [FrameworkIssue(code="CLI_CONFIG_LOAD_FAILED", message="Config load failed.", details={})],
            warnings=[],
            stats={"spaces_total": 0, "sources_total": 0, "skills_total": 0},
        )
        _dump_json_to_stdout(report.to_jsonable(), pretty=bool(args.pretty))
        return _exit_code_for_scan(report)

    mgr = SkillsManager(workspace_root=ws, skills_config=config.skills)
    try:
        report = mgr.scan()
    except FrameworkError:
        cached = mgr.last_scan_report
        if cached is not None:
            report = cached
        else:
            report = ScanReport(
                scan_id="scan_cli_error",
                skills=[],
                errors=[FrameworkIssue(code="CLI_SCAN_FAILED", message="Skill scan failed.", details={})],
                warnings=[],
                stats={"spaces_total": 0, "sources_total": 0, "skills_total": 0},
            )
    except Exception as exc:
        report = ScanReport(
            scan_id="scan_cli_error",
            skills=[],
            errors=[FrameworkIssue(code="CLI_SCAN_FAILED", message="Skill scan failed.", details={"reason": str(exc)})],
            warnings=[],
            stats={"spaces_total": 0, "sources_total": 0, "skills_total": 0},
        )

    _dump_json_to_stdout(report.to_jsonable(), pretty=bool(args.pretty))
    return _exit_code_for_scan(report)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    CLI 入口函数（用于 console_scripts 与测试）。

    参数：
    - argv：命令行参数列表（不含程序名）；为 None 时读取 sys.argv[1:]。

    返回：
    - int：exit code（不会直接 sys.exit，便于测试）。
    """

    # 入口期尽早确保 UTF-8 输出，避免 `C` locale 下 `--help`/JSON 输出触发 UnicodeEncodeError。
    ensure_utf8_stdio()

    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        # argparse 的约定：
        # - `--help`：exit code 0
        # - 参数错误：exit code 2
        code = getattr(exc, "code", 2)
        if code is None:
            return 2
        return int(code)

    if args.command == "tools":
        if args.tools_cmd == "list-dir":
            return _handle_tools_list_dir(args)
        if args.tools_cmd == "grep-files":
            return _handle_tools_grep_files(args)
        if args.tools_cmd == "apply-patch":
            return _handle_tools_apply_patch(args)
        if args.tools_cmd == "read-file":
            return _handle_tools_read_file(args)
        if args.tools_cmd == "shell":
            return _handle_tools_shell(args)
        if args.tools_cmd == "shell-command":
            return _handle_tools_shell_command(args)
        if args.tools_cmd == "exec-command":
            return _handle_tools_exec_command(args)
        if args.tools_cmd == "write-stdin":
            return _handle_tools_write_stdin(args)
        if args.tools_cmd == "update-plan":
            return _handle_tools_update_plan(args)
        if args.tools_cmd == "request-user-input":
            return _handle_tools_request_user_input(args)
        if args.tools_cmd == "view-image":
            return _handle_tools_view_image(args)
        if args.tools_cmd == "web-search":
            return _handle_tools_web_search(args)
        if args.tools_cmd == "spawn-agent":
            return _handle_tools_spawn_agent(args)
        if args.tools_cmd == "wait":
            return _handle_tools_wait(args)
        if args.tools_cmd == "send-input":
            return _handle_tools_send_input(args)
        if args.tools_cmd == "close-agent":
            return _handle_tools_close_agent(args)
        if args.tools_cmd == "resume-agent":
            return _handle_tools_resume_agent(args)

        # 未知 tools 子命令：仍输出 tools envelope，便于脚本处理
        ws, overlays, env_file, dotenv_error = _prepare_bootstrap_for_cli(args)
        workspace_root = ws or Path(str(args.workspace_root)).expanduser().resolve()
        result = ToolResult.error_payload(
            error_kind="validation",
            stderr="Unknown tools subcommand.",
            data={"subcommand": str(getattr(args, "tools_cmd", ""))},
        )
        _dump_tools_cli_payload(
            tool_name="tools",
            result=result,
            workspace_root=workspace_root,
            overlay_paths=overlays,
            env_file=env_file,
            dotenv_error=dotenv_error,
            pretty=bool(getattr(args, "pretty", False)),
        )
        return _exit_code_for_tool_result(result)

    if args.command == "runs":
        if args.runs_cmd != "metrics":
            # 统一输出 JSON（框架层）；未知子命令视为 validation
            payload = {"error_kind": "validation", "message": "Unknown runs subcommand.", "subcommand": args.runs_cmd}
            _dump_json_to_stdout(payload, pretty=bool(getattr(args, "pretty", False)))
            return 20

        ws, ws_issue = _resolve_workspace_root(str(args.workspace_root))
        if ws_issue is not None or ws is None:
            payload2 = {"error_kind": "validation", "message": "Workspace root is invalid.", "details": ws_issue.details if ws_issue else {}}
            _dump_json_to_stdout(payload2, pretty=bool(getattr(args, "pretty", False)))
            return 20

        events_path: Path
        if args.events_path is not None:
            p = Path(str(args.events_path)).expanduser()
            events_path = p.resolve() if p.is_absolute() else (ws / p).resolve()
        else:
            run_id = str(args.run_id or "").strip()
            events_path = (ws / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()

        if not events_path.exists():
            summary = compute_run_metrics_summary(events_path=events_path)
            _dump_json_to_stdout(summary, pretty=bool(getattr(args, "pretty", False)))
            return 22

        summary2 = compute_run_metrics_summary(events_path=events_path)
        _dump_json_to_stdout(summary2, pretty=bool(getattr(args, "pretty", False)))
        # 若 summary 自身已记录 invalid_wal，则视为 validation（20）
        if any((it or {}).get("kind") == "invalid_wal" for it in (summary2.get("errors") or [])):
            return 20
        return 0

    if args.command != "skills":
        payload = {
            "issues": [
                {"code": "CLI_COMMAND_INVALID", "message": "Unknown command.", "details": {"command": args.command}}
            ],
            "stats": {
                "workspace_root": str(Path.cwd().resolve()),
                "overlay_paths": [],
                "env_file": None,
                "issues_total": 1,
                "errors_total": 1,
                "warnings_total": 0,
            },
        }
        _dump_json_to_stdout(payload, pretty=bool(getattr(args, "pretty", False)))
        return 2

    if args.skills_cmd in {"preflight", "validate-config"}:
        return _handle_preflight(args)
    if args.skills_cmd == "scan":
        return _handle_scan(args)

    payload2 = {
        "issues": [
            {
                "code": "CLI_COMMAND_INVALID",
                "message": "Unknown skills subcommand.",
                "details": {"subcommand": args.skills_cmd},
            }
        ],
        "stats": {
            "workspace_root": str(Path.cwd().resolve()),
            "overlay_paths": [],
            "env_file": None,
            "issues_total": 1,
            "errors_total": 1,
            "warnings_total": 0,
        },
    }
    _dump_json_to_stdout(payload2, pretty=bool(getattr(args, "pretty", False)))
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
