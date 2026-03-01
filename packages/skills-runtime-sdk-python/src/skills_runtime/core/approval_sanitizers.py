"""
审批请求脱敏与摘要构建（纯函数）。

本模块从 `core.agent_loop` 拆出，目标：
- 保持行为等价；
- 将 shell 系工具的重复逻辑合并为“通用函数 + 工具配置”。
"""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from skills_runtime.safety.guard import CommandRisk, evaluate_command_risk
from skills_runtime.skills.mentions import extract_skill_mentions

if TYPE_CHECKING:  # pragma: no cover
    from skills_runtime.skills.manager import SkillsManager


def _format_argv(argv: list[str]) -> str:
    """
    将 argv 格式化为可读命令串（用于 approval summary）。

    说明：
    - 仅用于展示，不用于执行
    - 使用 shlex.quote，尽量让空格/特殊字符可读
    """

    return " ".join(shlex.quote(x) for x in argv)


def _parse_shellish_command_to_argv(command: str) -> tuple[list[str], bool, str]:
    """
    将 “shell string” 尽力解析为 argv（用于 allowlist/denylist 与 risk 评估）。

    约束与动机：
    - `shell_command` / `exec_command` 使用 `/bin/sh -lc <command>` 执行；
      若直接把 wrapper argv 交给 policy，会导致 allowlist/denylist 永远匹配不到（cmd0 会变成 /bin/sh）。
    - 但 shell string 可能包含管道/重定向/控制符等语法；这类命令应被视为“复杂”，即使 allowlist 命中也建议走 approvals（避免 `pytest && rm -rf /` 误放行）。

    返回：
    - argv：解析结果（可能为空）
    - is_complex：是否疑似包含 shell 控制语法（建议强制 approvals）
    - reason：用于审计/调试的简短原因（不用于执行）
    """

    s = str(command or "")
    if not s.strip():
        return [], True, "empty command"

    # fast path：明显复杂语法（best-effort；宁可误判为复杂，也不要误判为简单）
    if "\n" in s or "`" in s or "$(" in s:
        return [], True, "shell metacharacters detected"

    try:
        argv = shlex.split(s)
    except ValueError:
        return [], True, "shlex split failed"

    if not argv:
        return [], True, "empty argv after parse"

    # 若出现典型控制符/管道/重定向，则视为复杂命令（禁止 allowlist 直通）
    control_tokens = {"&&", "||", ";", "|", "|&", "&"}
    for tok in argv:
        if tok in control_tokens:
            return argv, True, f"control token detected: {tok}"
        if tok.startswith(">") or tok.startswith("<"):
            return argv, True, "redirection token detected"
    return argv, False, "parsed"


def _extract_env_keys(args: Dict[str, Any]) -> list[str]:
    """提取 env 键列表（仅键名，不含值）。"""

    env_raw = args.get("env")
    if isinstance(env_raw, dict):
        return sorted(str(k) for k in env_raw.keys())
    return []


def _extract_optional_str(args: Dict[str, Any], key: str) -> Optional[str]:
    """提取并标准化可选字符串字段；空白值返回 None。"""

    v = args.get(key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


@dataclass(frozen=True)
class _ShellToolConfig:
    """shell 工具分支配置，供通用 sanitizer 使用。"""

    tool: str
    mode: str  # argv | command
    input_key: str
    summary_action: str
    summary_empty_value: str
    output_command_key: Optional[str] = None
    cwd_input_key: Optional[str] = None
    cwd_output_key: Optional[str] = None
    timeout_input_key: Optional[str] = None
    timeout_output_key: Optional[str] = None
    include_tty: bool = False
    tty_default_true_if_missing: bool = False
    include_env_keys: bool = False
    include_intent: bool = False
    passthrough_fields: tuple[tuple[str, str], ...] = ()


_SHELL_TOOL_CONFIGS: dict[str, _ShellToolConfig] = {
    "shell_exec": _ShellToolConfig(
        tool="shell_exec",
        mode="argv",
        input_key="argv",
        summary_action="执行命令",
        summary_empty_value="<invalid argv>",
        cwd_input_key="cwd",
        cwd_output_key="cwd",
        timeout_input_key="timeout_ms",
        timeout_output_key="timeout_ms",
        include_tty=True,
        tty_default_true_if_missing=False,
        include_env_keys=True,
    ),
    "shell": _ShellToolConfig(
        tool="shell",
        mode="argv",
        input_key="command",
        summary_action="执行命令",
        summary_empty_value="<invalid argv>",
        cwd_input_key="workdir",
        cwd_output_key="cwd",
        timeout_input_key="timeout_ms",
        timeout_output_key="timeout_ms",
        include_tty=True,
        tty_default_true_if_missing=False,
        include_env_keys=True,
    ),
    "shell_command": _ShellToolConfig(
        tool="shell_command",
        mode="command",
        input_key="command",
        summary_action="执行命令",
        summary_empty_value="<empty>",
        output_command_key="command",
        cwd_input_key="workdir",
        cwd_output_key="workdir",
        timeout_input_key="timeout_ms",
        timeout_output_key="timeout_ms",
        include_tty=False,
        include_env_keys=True,
        include_intent=True,
    ),
    "exec_command": _ShellToolConfig(
        tool="exec_command",
        mode="command",
        input_key="cmd",
        summary_action="启动命令",
        summary_empty_value="<empty>",
        output_command_key="cmd",
        cwd_input_key="workdir",
        cwd_output_key="workdir",
        include_tty=True,
        tty_default_true_if_missing=True,
        include_env_keys=False,
        include_intent=True,
        passthrough_fields=(("yield_time_ms", "yield_time_ms"), ("max_output_tokens", "max_output_tokens")),
    ),
}


def _sanitize_shell_like_approval_request(
    tool: str,
    *,
    args: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    """通用 shell 分支：基于工具配置构建审批摘要与脱敏请求。"""

    cfg = _SHELL_TOOL_CONFIGS[tool]
    sandbox_policy = _extract_optional_str(args, "sandbox")
    sandbox_perm = _extract_optional_str(args, "sandbox_permissions")
    justification = _extract_optional_str(args, "justification")

    req: Dict[str, Any] = {
        "sandbox": sandbox_policy,
        "sandbox_permissions": sandbox_perm,
    }
    if cfg.include_env_keys:
        req["env_keys"] = _extract_env_keys(args)

    if cfg.mode == "argv":
        argv_raw = args.get(cfg.input_key)
        argv: list[str] = argv_raw if isinstance(argv_raw, list) and all(isinstance(x, str) for x in argv_raw) else []
        risk = evaluate_command_risk(argv) if argv else evaluate_command_risk([""])
        req["argv"] = argv
        cmd_display = _format_argv(argv) if argv else cfg.summary_empty_value
        parse_reason = ""
    else:
        command_raw = args.get(cfg.input_key)
        command_str = command_raw.strip() if isinstance(command_raw, str) else ""
        argv, is_complex, parse_reason = _parse_shellish_command_to_argv(command_str)
        risk = (
            CommandRisk(risk_level="high", reason=parse_reason)
            if is_complex
            else (evaluate_command_risk(argv) if argv else evaluate_command_risk([""]))
        )
        req[cfg.output_command_key or cfg.input_key] = command_str
        if cfg.include_intent:
            req["intent"] = {"argv": argv, "is_complex": bool(is_complex), "reason": parse_reason}
        cmd_display = command_str or cfg.summary_empty_value

    if cfg.cwd_input_key is not None and cfg.cwd_output_key is not None:
        req[cfg.cwd_output_key] = args.get(cfg.cwd_input_key)

    if cfg.timeout_input_key is not None and cfg.timeout_output_key is not None:
        req[cfg.timeout_output_key] = args.get(cfg.timeout_input_key)

    for out_key, in_key in cfg.passthrough_fields:
        req[out_key] = args.get(in_key)

    if cfg.include_tty:
        tty_raw = args.get("tty")
        tty_bool = True if (cfg.tty_default_true_if_missing and tty_raw is None) else bool(tty_raw or False)
        req["tty"] = tty_bool

    req["risk"] = {"risk_level": risk.risk_level, "reason": risk.reason}
    if justification is not None:
        req["justification"] = justification

    summary = f"授权：{tool} {cfg.summary_action}：{cmd_display}（risk={risk.risk_level}）"
    return summary, req


def _sanitize_approval_request(
    tool: str,
    *,
    args: Dict[str, Any],
    skills_manager: Optional[SkillsManager] = None,
) -> tuple[str, Dict[str, Any]]:
    """
    将 tool args 转成“可审计但不泄露 secrets”的 approval 请求表示。

    Gate：
    - 不得包含 env 值 / API key / file content 明文
    - 对 file_write：允许 content 的不可逆摘要（sha256）与 bytes
    """

    if tool in _SHELL_TOOL_CONFIGS:
        return _sanitize_shell_like_approval_request(tool, args=args)

    if tool == "write_stdin":
        session_id = args.get("session_id")
        chars = args.get("chars")
        yield_time_ms = args.get("yield_time_ms")
        max_output_tokens = args.get("max_output_tokens")

        bytes_count: Optional[int] = None
        sha256: Optional[str] = None
        if isinstance(chars, str):
            b = chars.encode("utf-8")
            bytes_count = len(b)
            sha256 = hashlib.sha256(b).hexdigest()

        req_ws: Dict[str, Any] = {
            "session_id": session_id,
            "yield_time_ms": yield_time_ms,
            "max_output_tokens": max_output_tokens,
            "bytes": bytes_count,
            "chars_sha256": sha256,
            "is_poll": bool(chars is None or chars == ""),
        }
        summary_ws = f"授权：write_stdin 写入 session：{session_id}（{bytes_count if bytes_count is not None else 0} bytes）"
        return summary_ws, req_ws

    if tool == "file_write":
        path = args.get("path")
        content = args.get("content")
        create_dirs = args.get("create_dirs")
        if create_dirs is None:
            create_dirs = args.get("mkdirs")
        create_dirs = True if create_dirs is None else bool(create_dirs)

        sandbox_perm = _extract_optional_str(args, "sandbox_permissions")

        bytes_count: Optional[int] = None
        sha256: Optional[str] = None
        if isinstance(content, str):
            b = content.encode("utf-8")
            bytes_count = len(b)
            sha256 = hashlib.sha256(b).hexdigest()

        req2: Dict[str, Any] = {
            "path": path,
            "create_dirs": create_dirs,
            "sandbox_permissions": sandbox_perm,
            "bytes": bytes_count,
            "content_sha256": sha256,
        }
        justification = _extract_optional_str(args, "justification")
        if justification is not None:
            req2["justification"] = justification
        summary2 = f"授权：file_write 写入文件：{path}（{bytes_count if bytes_count is not None else '?'} bytes）"
        return summary2, req2

    if tool == "apply_patch":
        input_text = args.get("input")

        bytes_count: Optional[int] = None
        sha256: Optional[str] = None
        if isinstance(input_text, str):
            b = input_text.encode("utf-8")
            bytes_count = len(b)
            sha256 = hashlib.sha256(b).hexdigest()

        # best-effort：提取受影响路径（不解析完整语法，不做 I/O）
        file_paths: list[str] = []
        if isinstance(input_text, str):
            for line in input_text.splitlines():
                s = line.strip()
                for prefix in ("*** Add File: ", "*** Update File: ", "*** Delete File: "):
                    if s.startswith(prefix):
                        file_paths.append(s[len(prefix) :].strip())
                if s.startswith("*** Move to: "):
                    file_paths.append(s[len("*** Move to: ") :].strip())

        req_ap: Dict[str, Any] = {
            "file_paths": file_paths,
            "bytes": bytes_count,
            "content_sha256": sha256,
        }
        summary_ap = f"授权：apply_patch 应用补丁（{bytes_count if bytes_count is not None else '?'} bytes）"
        return summary_ap, req_ap

    if tool == "skill_exec":
        mention = args.get("skill_mention")
        action_id = args.get("action_id")
        mention_str = mention.strip() if isinstance(mention, str) else ""
        action_str = action_id.strip() if isinstance(action_id, str) else ""

        req3: Dict[str, Any] = {"skill_mention": mention_str, "action_id": action_str}

        argv: list[str] = []
        timeout_ms: Optional[int] = None
        env_keys: list[str] = []
        bundle_root: Optional[str] = None
        bundle_sha256: Optional[str] = None
        resolve_error: Optional[str] = None

        if skills_manager is not None and mention_str and action_str:
            try:
                # 严格：approval/audit 口径尽量与工具参数校验一致，避免对无效 token 做“误解析”。
                stripped = mention_str.strip()
                mentions = extract_skill_mentions(stripped)
                if len(mentions) != 1 or mentions[0].mention_text != stripped:
                    raise ValueError("not_a_single_full_token")

                resolved = skills_manager.resolve_mentions(mention_str)
                if resolved:
                    skill, _m = resolved[0]
                    if skill.path is not None:
                        bundle_root = str(Path(skill.path).parent.resolve())
                    else:
                        # Redis bundle-backed skills：在 approval/audit 阶段允许 lazy fetch + extract
                        br, sha = skills_manager.get_bundle_root_for_tool(skill=skill, purpose="actions")
                        bundle_root = str(Path(br).resolve())
                        bundle_sha256 = str(sha) if sha else None
                    actions = (skill.metadata or {}).get("actions")
                    if isinstance(actions, dict):
                        adef = actions.get(action_str)
                        if isinstance(adef, dict):
                            argv_raw = adef.get("argv")
                            if isinstance(argv_raw, list) and all(isinstance(x, str) for x in argv_raw):
                                argv = list(argv_raw)
                                # 尽量 materialize argv（与 tool 行为对齐），便于审批可视化与 TOCTOU 绑定。
                                # 注意：materialize 失败不应让 argv/env_keys 丢失（否则会导致 policy/approval 误判为“无命令”）。
                                if bundle_root:
                                    try:
                                        actions_dir = (Path(bundle_root) / "actions").resolve()
                                        out = list(argv)
                                        for i in range(1, len(out)):
                                            raw = out[i]
                                            if not raw:
                                                continue
                                            looks_like_path = ("/" in raw) or raw.startswith(".")
                                            if not looks_like_path:
                                                continue
                                            if raw.startswith("/") or not raw.startswith("actions/"):
                                                raise ValueError("argv_path_escape")
                                            p = Path(raw)
                                            if any(part == ".." for part in p.parts):
                                                raise ValueError("argv_path_escape")
                                            resolved_p = (Path(bundle_root) / p).resolve()
                                            if not resolved_p.is_relative_to(actions_dir):
                                                raise ValueError("argv_path_escape")
                                            if not resolved_p.exists() or not resolved_p.is_file():
                                                raise ValueError("argv_path_invalid")
                                            out[i] = str(resolved_p)
                                        argv = out
                                    except (OSError, ValueError) as exc:
                                        resolve_error = str(exc)
                            tm = adef.get("timeout_ms")
                            if tm is not None:
                                try:
                                    timeout_ms = int(tm)
                                except (TypeError, ValueError):
                                    timeout_ms = None
                            env_raw = adef.get("env")
                            if isinstance(env_raw, dict):
                                env_keys = sorted(str(k) for k in env_raw.keys())
            except (KeyError, AttributeError, TypeError, ValueError) as e:
                resolve_error = str(e)

        risk = evaluate_command_risk(argv) if argv else evaluate_command_risk([""])
        req3.update(
            {
                "bundle_root": bundle_root,
                "bundle_sha256": bundle_sha256,
                "argv": argv,
                "timeout_ms": timeout_ms,
                "env_keys": env_keys,
                "resolve_error": resolve_error,
                "risk": {"risk_level": risk.risk_level, "reason": risk.reason},
            }
        )

        # 生成可复现的 action 指纹，避免“动作内容变化但 approval_key 复用”。
        try:
            fingerprint_obj = {
                "skill_mention": mention_str,
                "action_id": action_str,
                "bundle_root": bundle_root,
                "bundle_sha256": bundle_sha256,
                "argv": argv,
                "timeout_ms": timeout_ms,
                "env_keys": env_keys,
            }
            fingerprint = json.dumps(fingerprint_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
            req3["action_sha256"] = hashlib.sha256(fingerprint).hexdigest()
        except (KeyError, TypeError, ValueError):
            # fail-open：只影响缓存粒度，不应阻塞执行
            pass

        cmd = _format_argv(argv) if argv else "<unresolved action argv>"
        summary3 = f"授权：skill_exec 执行动作：{mention_str}#{action_str} => {cmd}（risk={risk.risk_level}）"
        return summary3, req3

    # fallback：未知 tool 仅记录 keys
    keys: list[str] = []
    if isinstance(args, dict):
        keys = sorted(str(k) for k in args.keys())
    return f"授权：{tool}", {"arguments_keys": keys}


__all__ = [
    "_format_argv",
    "_parse_shellish_command_to_argv",
    "_sanitize_approval_request",
]
