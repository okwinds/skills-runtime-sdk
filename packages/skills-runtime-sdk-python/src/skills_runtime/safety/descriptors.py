"""
内置工具安全描述符（Phase 2）。

职责：
- 统一提取 tool 风险信息（供 policy 判定）
- 统一生成 approvals/event 的脱敏参数表示
- 避免在 `core/agent.py` 内堆叠大量 tool 分支
"""

from __future__ import annotations

import hashlib
import json
import logging
import shlex
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from skills_runtime.safety.guard import CommandRisk, evaluate_command_risk
from skills_runtime.skills.mentions import extract_skill_mentions

logger = logging.getLogger(__name__)
from skills_runtime.tools.protocol import ToolSafetyDescriptor

if TYPE_CHECKING:  # pragma: no cover
    from skills_runtime.skills.manager import SkillsManager


def format_argv(argv: list[str]) -> str:
    """
    将 argv 格式化为可读命令串（仅用于展示，不用于执行）。

    参数：
    - argv：命令参数数组

    返回：
    - 适合展示的 shell 风格字符串
    """

    return " ".join(shlex.quote(x) for x in argv)


def parse_shellish_command_to_argv(command: str) -> tuple[list[str], bool, str]:
    """
    将 shell 字符串尽力解析为 argv，并标记是否是复杂命令。

    参数：
    - command：shell 字符串

    返回：
    - argv：解析后的参数数组
    - is_complex：是否包含控制符/重定向等复杂语法
    - reason：解析原因（用于审计）
    """

    s = str(command or "")
    if not s.strip():
        return [], True, "empty command"

    # 保守策略：复杂语法宁可误判复杂，也不误判为简单。
    if "\n" in s or "`" in s or "$(" in s:
        return [], True, "shell metacharacters detected"

    try:
        argv = shlex.split(s)
    except ValueError:
        return [], True, "shlex split failed"

    if not argv:
        return [], True, "empty argv after parse"

    control_tokens = {"&&", "||", ";", "|", "|&", "&"}
    for tok in argv:
        if tok in control_tokens:
            return argv, True, f"control token detected: {tok}"
        if tok.startswith(">") or tok.startswith("<"):
            return argv, True, "redirection token detected"

    return argv, False, "parsed"


def _env_keys_from_args(args: Dict[str, Any]) -> list[str]:
    """从 args 中提取 env 键名列表（不暴露值）。"""

    env_raw = args.get("env")
    if not isinstance(env_raw, dict):
        return []
    return sorted(str(k) for k in env_raw.keys())


def _normalized_optional_str(value: Any) -> Optional[str]:
    """将可选字符串标准化为去空白后的值；空值返回 None。"""

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _text_digest(text: Any) -> tuple[Optional[int], Optional[str]]:
    """返回文本的字节数和 sha256；非字符串输入返回 (None, None)。"""

    if not isinstance(text, str):
        return None, None
    b = text.encode("utf-8")
    return len(b), hashlib.sha256(b).hexdigest()


def _extract_apply_patch_file_paths(input_text: Any) -> list[str]:
    """从 apply_patch 输入中提取受影响文件路径（best-effort）。"""

    if not isinstance(input_text, str):
        return []

    out: list[str] = []
    for line in input_text.splitlines():
        s = line.strip()
        for prefix in ("*** Add File: ", "*** Update File: ", "*** Delete File: "):
            if s.startswith(prefix):
                out.append(s[len(prefix) :].strip())
        if s.startswith("*** Move to: "):
            out.append(s[len("*** Move to: ") :].strip())
    return out


class ShellExecDescriptor:
    """`shell_exec` 的安全描述符。"""

    policy_category = "shell"

    def extract_risk(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """提取 `shell_exec` 风险信息。"""

        _ = ctx
        argv_raw = args.get("argv")
        argv = argv_raw if isinstance(argv_raw, list) and all(isinstance(x, str) for x in argv_raw) else []
        risk = evaluate_command_risk(argv) if argv else evaluate_command_risk([""])
        return {
            "argv": argv,
            "is_complex": False,
            "risk_level": risk.risk_level,
            "reason": risk.reason,
        }

    def sanitize_for_approval(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `shell_exec` 的 approvals 脱敏请求。"""

        _ = ctx
        risk = self.extract_risk(args)
        req: Dict[str, Any] = {
            "argv": risk["argv"],
            "cwd": args.get("cwd"),
            "timeout_ms": args.get("timeout_ms"),
            "tty": bool(args.get("tty") or False),
            "env_keys": _env_keys_from_args(args),
            "sandbox": _normalized_optional_str(args.get("sandbox")),
            "sandbox_permissions": _normalized_optional_str(args.get("sandbox_permissions")),
            "risk": {
                "risk_level": risk["risk_level"],
                "reason": risk["reason"],
            },
        }
        justification = _normalized_optional_str(args.get("justification"))
        if justification is not None:
            req["justification"] = justification
        return req

    def sanitize_for_event(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `shell_exec` 的 event/WAL 脱敏参数。"""

        return self.sanitize_for_approval(args, **ctx)


class ShellDescriptor:
    """`shell` 的安全描述符（语义复用 shell_exec，但字段为 `command`）。"""

    policy_category = "shell"

    def extract_risk(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """提取 `shell` 风险信息。"""

        _ = ctx
        argv_raw = args.get("command")
        argv = argv_raw if isinstance(argv_raw, list) and all(isinstance(x, str) for x in argv_raw) else []
        risk = evaluate_command_risk(argv) if argv else evaluate_command_risk([""])
        return {
            "argv": argv,
            "is_complex": False,
            "risk_level": risk.risk_level,
            "reason": risk.reason,
        }

    def sanitize_for_approval(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `shell` 的 approvals 脱敏请求。"""

        _ = ctx
        risk = self.extract_risk(args)
        req: Dict[str, Any] = {
            "argv": risk["argv"],
            "cwd": args.get("workdir"),
            "timeout_ms": args.get("timeout_ms"),
            "tty": bool(args.get("tty") or False),
            "env_keys": _env_keys_from_args(args),
            "sandbox": _normalized_optional_str(args.get("sandbox")),
            "sandbox_permissions": _normalized_optional_str(args.get("sandbox_permissions")),
            "risk": {
                "risk_level": risk["risk_level"],
                "reason": risk["reason"],
            },
        }
        justification = _normalized_optional_str(args.get("justification"))
        if justification is not None:
            req["justification"] = justification
        return req

    def sanitize_for_event(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `shell` 的 event/WAL 脱敏参数。"""

        return self.sanitize_for_approval(args, **ctx)


class ShellCommandDescriptor:
    """`shell_command` 的安全描述符（字符串命令，先做 shell 解析）。"""

    policy_category = "shell"

    def extract_risk(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """提取 `shell_command` 风险信息。"""

        _ = ctx
        cmd_raw = args.get("command")
        cmd_str = cmd_raw.strip() if isinstance(cmd_raw, str) else ""
        argv, is_complex, parse_reason = parse_shellish_command_to_argv(cmd_str)
        if is_complex:
            risk = CommandRisk(risk_level="high", reason=parse_reason)
        else:
            risk = evaluate_command_risk(argv) if argv else evaluate_command_risk([""])
        return {
            "argv": argv,
            "is_complex": bool(is_complex),
            "risk_level": risk.risk_level,
            "reason": risk.reason,
            "parse_reason": parse_reason,
            "command": cmd_str,
        }

    def sanitize_for_approval(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `shell_command` 的 approvals 脱敏请求。"""

        _ = ctx
        risk = self.extract_risk(args)
        req: Dict[str, Any] = {
            "command": risk["command"],
            "workdir": args.get("workdir"),
            "timeout_ms": args.get("timeout_ms"),
            "env_keys": _env_keys_from_args(args),
            "sandbox": _normalized_optional_str(args.get("sandbox")),
            "sandbox_permissions": _normalized_optional_str(args.get("sandbox_permissions")),
            "intent": {
                "argv": risk["argv"],
                "is_complex": risk["is_complex"],
                "reason": risk["parse_reason"],
            },
            "risk": {
                "risk_level": risk["risk_level"],
                "reason": risk["reason"],
            },
        }
        justification = _normalized_optional_str(args.get("justification"))
        if justification is not None:
            req["justification"] = justification
        return req

    def sanitize_for_event(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `shell_command` 的 event/WAL 脱敏参数。"""

        return self.sanitize_for_approval(args, **ctx)


class ExecCommandDescriptor:
    """`exec_command` 的安全描述符（字符串命令，字段名为 `cmd`）。"""

    policy_category = "shell"

    def extract_risk(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """提取 `exec_command` 风险信息。"""

        _ = ctx
        cmd_raw = args.get("cmd")
        cmd_str = cmd_raw.strip() if isinstance(cmd_raw, str) else ""
        argv, is_complex, parse_reason = parse_shellish_command_to_argv(cmd_str)
        if is_complex:
            risk = CommandRisk(risk_level="high", reason=parse_reason)
        else:
            risk = evaluate_command_risk(argv) if argv else evaluate_command_risk([""])
        return {
            "argv": argv,
            "is_complex": bool(is_complex),
            "risk_level": risk.risk_level,
            "reason": risk.reason,
            "parse_reason": parse_reason,
            "cmd": cmd_str,
        }

    def sanitize_for_approval(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `exec_command` 的 approvals 脱敏请求。"""

        _ = ctx
        risk = self.extract_risk(args)
        tty_raw = args.get("tty")
        tty_bool = True if tty_raw is None else bool(tty_raw)

        req: Dict[str, Any] = {
            "cmd": risk["cmd"],
            "workdir": args.get("workdir"),
            "yield_time_ms": args.get("yield_time_ms"),
            "max_output_tokens": args.get("max_output_tokens"),
            "tty": tty_bool,
            "sandbox": _normalized_optional_str(args.get("sandbox")),
            "sandbox_permissions": _normalized_optional_str(args.get("sandbox_permissions")),
            "intent": {
                "argv": risk["argv"],
                "is_complex": risk["is_complex"],
                "reason": risk["parse_reason"],
            },
            "risk": {
                "risk_level": risk["risk_level"],
                "reason": risk["reason"],
            },
        }
        justification = _normalized_optional_str(args.get("justification"))
        if justification is not None:
            req["justification"] = justification
        return req

    def sanitize_for_event(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `exec_command` 的 event/WAL 脱敏参数。"""

        return self.sanitize_for_approval(args, **ctx)


class FileWriteDescriptor:
    """`file_write` 的安全描述符。"""

    policy_category = "file"

    def extract_risk(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """`file_write` 不执行 shell，返回固定低风险。"""

        _ = args
        _ = ctx
        return {
            "argv": [],
            "is_complex": False,
            "risk_level": "low",
            "reason": "file_write is treated as non-shell operation",
        }

    def sanitize_for_approval(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `file_write` 的 approvals 脱敏请求。"""

        _ = ctx
        create_dirs = args.get("create_dirs")
        if create_dirs is None:
            create_dirs = args.get("mkdirs")
        create_dirs = True if create_dirs is None else bool(create_dirs)

        bytes_count, sha256 = _text_digest(args.get("content"))

        req: Dict[str, Any] = {
            "path": args.get("path"),
            "create_dirs": create_dirs,
            "sandbox_permissions": _normalized_optional_str(args.get("sandbox_permissions")),
            "bytes": bytes_count,
            "content_sha256": sha256,
        }
        justification = _normalized_optional_str(args.get("justification"))
        if justification is not None:
            req["justification"] = justification
        return req

    def sanitize_for_event(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `file_write` 的 event/WAL 脱敏参数。"""

        return self.sanitize_for_approval(args, **ctx)


class ApplyPatchDescriptor:
    """`apply_patch` 的安全描述符。"""

    policy_category = "file"

    def extract_risk(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """`apply_patch` 不执行 shell，返回固定低风险。"""

        _ = args
        _ = ctx
        return {
            "argv": [],
            "is_complex": False,
            "risk_level": "low",
            "reason": "apply_patch is treated as non-shell operation",
        }

    def sanitize_for_approval(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `apply_patch` 的 approvals 脱敏请求。"""

        _ = ctx
        input_text = args.get("input")
        bytes_count, sha256 = _text_digest(input_text)
        return {
            "file_paths": _extract_apply_patch_file_paths(input_text),
            "bytes": bytes_count,
            "content_sha256": sha256,
        }

    def sanitize_for_event(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `apply_patch` 的 event/WAL 脱敏参数。"""

        return self.sanitize_for_approval(args, **ctx)


class SkillExecDescriptor:
    """`skill_exec` 的安全描述符（执行本质为 shell action）。"""

    policy_category = "shell"

    def _resolve_action_intent(
        self,
        *,
        mention_str: str,
        action_str: str,
        skills_manager: Optional["SkillsManager"],
    ) -> Dict[str, Any]:
        """
        解析 `skill_exec` 对应 action 的命令意图（best-effort）。

        返回字段与旧实现保持兼容：
        - bundle_root/bundle_sha256/argv/timeout_ms/env_keys/resolve_error
        """

        argv: list[str] = []
        timeout_ms: Optional[int] = None
        env_keys: list[str] = []
        bundle_root: Optional[str] = None
        bundle_sha256: Optional[str] = None
        resolve_error: Optional[str] = None

        if skills_manager is not None and mention_str and action_str:
            try:
                stripped = mention_str.strip()
                mentions = extract_skill_mentions(stripped)
                if len(mentions) != 1 or mentions[0].mention_text != stripped:
                    raise ValueError("not_a_single_full_token")

                resolved = skills_manager.resolve_mentions(mention_str)
                if resolved:
                    skill, _mention = resolved[0]
                    if skill.path is not None:
                        bundle_root = str(Path(skill.path).parent.resolve())
                    else:
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
            except Exception as exc:  # 防御性兜底：resolve_mentions 可能抛出 FrameworkError（namespace 未配置）等。
                resolve_error = str(exc)

        return {
            "bundle_root": bundle_root,
            "bundle_sha256": bundle_sha256,
            "argv": argv,
            "timeout_ms": timeout_ms,
            "env_keys": env_keys,
            "resolve_error": resolve_error,
        }

    def extract_risk(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """提取 `skill_exec` 风险信息。"""

        mention = args.get("skill_mention")
        action_id = args.get("action_id")
        mention_str = mention.strip() if isinstance(mention, str) else ""
        action_str = action_id.strip() if isinstance(action_id, str) else ""
        skills_manager = ctx.get("skills_manager")

        intent = self._resolve_action_intent(
            mention_str=mention_str,
            action_str=action_str,
            skills_manager=skills_manager,
        )
        argv = intent["argv"] if isinstance(intent.get("argv"), list) else []
        risk = evaluate_command_risk(argv) if argv else evaluate_command_risk([""])

        return {
            "skill_mention": mention_str,
            "action_id": action_str,
            "argv": argv,
            "is_complex": False,
            "risk_level": risk.risk_level,
            "reason": risk.reason,
            "intent": intent,
        }

    def sanitize_for_approval(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `skill_exec` 的 approvals 脱敏请求。"""

        risk = self.extract_risk(args, **ctx)
        intent = risk["intent"]
        req: Dict[str, Any] = {
            "skill_mention": risk["skill_mention"],
            "action_id": risk["action_id"],
            "bundle_root": intent.get("bundle_root"),
            "bundle_sha256": intent.get("bundle_sha256"),
            "argv": risk["argv"],
            "timeout_ms": intent.get("timeout_ms"),
            "env_keys": intent.get("env_keys"),
            "resolve_error": intent.get("resolve_error"),
            "risk": {
                "risk_level": risk["risk_level"],
                "reason": risk["reason"],
            },
        }

        # 生成 action 指纹，防止动作内容变化导致审批缓存错用。
        try:
            fingerprint_obj = {
                "skill_mention": req["skill_mention"],
                "action_id": req["action_id"],
                "bundle_root": req.get("bundle_root"),
                "bundle_sha256": req.get("bundle_sha256"),
                "argv": req.get("argv"),
                "timeout_ms": req.get("timeout_ms"),
                "env_keys": req.get("env_keys"),
            }
            fingerprint = json.dumps(fingerprint_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
            req["action_sha256"] = hashlib.sha256(fingerprint).hexdigest()
        except (KeyError, TypeError, ValueError):
            # 防御性兜底：指纹生成失败不应阻断审批流程，跳过即可。
            pass

        return req

    def sanitize_for_event(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `skill_exec` 的 event/WAL 脱敏参数。"""

        return self.sanitize_for_approval(args, **ctx)


class WriteStdinDescriptor:
    """`write_stdin` 的安全描述符。"""

    policy_category = "file"

    def extract_risk(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """`write_stdin` 不走 shell policy，返回固定低风险。"""

        _ = args
        _ = ctx
        return {
            "argv": [],
            "is_complex": False,
            "risk_level": "low",
            "reason": "write_stdin is treated as non-shell operation",
        }

    def sanitize_for_approval(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `write_stdin` 的 approvals 脱敏请求。"""

        _ = ctx
        bytes_count, sha256 = _text_digest(args.get("chars"))
        chars = args.get("chars")
        return {
            "session_id": args.get("session_id"),
            "yield_time_ms": args.get("yield_time_ms"),
            "max_output_tokens": args.get("max_output_tokens"),
            "bytes": bytes_count,
            "chars_sha256": sha256,
            "is_poll": bool(chars is None or chars == ""),
        }

    def sanitize_for_event(self, args: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        """生成 `write_stdin` 的 event/WAL 脱敏参数。"""

        return self.sanitize_for_approval(args, **ctx)


_DESCRIPTOR_MAP: Dict[str, ToolSafetyDescriptor] = {
    "shell_exec": ShellExecDescriptor(),
    "shell": ShellDescriptor(),
    "shell_command": ShellCommandDescriptor(),
    "exec_command": ExecCommandDescriptor(),
    "file_write": FileWriteDescriptor(),
    "apply_patch": ApplyPatchDescriptor(),
    "skill_exec": SkillExecDescriptor(),
    "write_stdin": WriteStdinDescriptor(),
}


def get_builtin_tool_safety_descriptor(tool: str) -> Optional[ToolSafetyDescriptor]:
    """
    获取内置工具的安全描述符。

    参数：
    - tool：工具名

    返回：
    - 描述符实例；未知工具返回 None
    """

    return _DESCRIPTOR_MAP.get(str(tool or ""))


__all__ = [
    "ApplyPatchDescriptor",
    "ExecCommandDescriptor",
    "FileWriteDescriptor",
    "ShellCommandDescriptor",
    "ShellDescriptor",
    "ShellExecDescriptor",
    "SkillExecDescriptor",
    "WriteStdinDescriptor",
    "format_argv",
    "get_builtin_tool_safety_descriptor",
    "parse_shellish_command_to_argv",
]
