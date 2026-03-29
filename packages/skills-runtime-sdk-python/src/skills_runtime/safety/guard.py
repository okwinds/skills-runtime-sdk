"""
命令安全检测（Guard，Phase 2）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/safety.md` §1（最小危险模式检测）
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import List, Optional


@dataclass(frozen=True)
class CommandRisk:
    """命令风险评估输出。"""

    risk_level: str  # low|medium|high
    reason: str


def _argv_join(argv: List[str]) -> str:
    """将 argv 拼为可读字符串（仅用于规则匹配与 reason 生成，不用于执行）。"""

    return " ".join(argv)


def _cmd_basename(cmd: str) -> str:
    """提取命令 basename，兼容绝对路径调用（如 /usr/bin/sudo）。"""

    try:
        return PurePosixPath(cmd).name or cmd
    except Exception:
        return cmd


def _unwrap_shell_wrapped_command(argv: List[str]) -> Optional[List[str]]:
    """best-effort 展开 `env <shell> -c ...` / `<shell> -c ...` 的内层命令。"""

    if not argv:
        return None

    cmd_base = _cmd_basename(argv[0])
    inner = list(argv[1:])
    if cmd_base == "env":
        while inner and "=" in inner[0] and not inner[0].startswith("-"):
            inner.pop(0)
        if not inner:
            return None
        cmd_base = _cmd_basename(inner[0])
        inner = inner[1:]

    if cmd_base not in {"sh", "bash", "zsh", "fish"}:
        return None

    try:
        cmd_idx = inner.index("-c")
    except ValueError:
        return None
    if cmd_idx + 1 >= len(inner):
        return None

    command_text = str(inner[cmd_idx + 1] or "").strip()
    if not command_text:
        return None
    try:
        nested = shlex.split(command_text)
    except ValueError:
        nested = [command_text]
    return nested or None


def evaluate_command_risk(argv: List[str]) -> CommandRisk:
    """
    对 argv 做最小危险模式检测。

    参数：
    - argv：命令与参数（argv 形式）

    返回：
    - CommandRisk：risk_level + reason
    """

    if not argv:
        return CommandRisk(risk_level="medium", reason="空 argv（无法评估）")

    nested_argv = _unwrap_shell_wrapped_command(argv)
    if nested_argv is not None:
        return evaluate_command_risk(nested_argv)

    cmd0 = argv[0]
    cmd_base = _cmd_basename(cmd0)

    # sudo：默认高危（可能突破权限边界）
    if cmd_base == "sudo":
        return CommandRisk(risk_level="high", reason="检测到 sudo")

    # rm -rf / 或 rm -rf ~
    if cmd_base == "rm":
        has_recursive = False
        has_force = False
        targets: List[str] = []
        for token in argv[1:]:
            if token.startswith("--"):
                if token in {"--recursive", "--dir"}:
                    has_recursive = True
                if token == "--force":
                    has_force = True
                continue
            if token.startswith("-") and len(token) > 1:
                flags = token[1:]
                if "r" in flags or "R" in flags:
                    has_recursive = True
                if "f" in flags:
                    has_force = True
                continue
            targets.append(token)

        if has_recursive and has_force:
            for target in targets:
                t = target.strip()
                if t == "/" or t.startswith("/"):
                    return CommandRisk(risk_level="high", reason="检测到 rm -rf 可能删除根目录")
                if t == "~" or t.startswith("~/"):
                    return CommandRisk(risk_level="high", reason="检测到 rm -rf 可能删除 home 目录")

    # 可能破坏系统的数据盘/文件系统操作
    dangerous_prefixes = ("mkfs", "dd", "shutdown", "reboot", "halt", "poweroff")
    if any(cmd_base.endswith(p) or cmd_base == p for p in dangerous_prefixes):
        return CommandRisk(risk_level="high", reason=f"检测到高危命令：{cmd0}")
    if cmd_base == "systemctl" and any(str(token).strip().lower() in {"halt", "poweroff", "reboot"} for token in argv[1:]):
        return CommandRisk(risk_level="high", reason="检测到高危命令：systemctl")

    # 权限/归属批量变更：中风险（可能造成大面积权限漂移）
    if cmd_base in {"chmod", "chown", "chgrp"}:
        return CommandRisk(risk_level="medium", reason=f"检测到权限变更命令：{cmd_base}")

    # 默认：低风险（Phase 2 不做复杂白名单/解析）
    return CommandRisk(risk_level="low", reason="未命中高危模式（Phase 2 最小规则）")
