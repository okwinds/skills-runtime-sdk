"""
命令安全检测（Guard，Phase 2）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/safety.md` §1（最小危险模式检测）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class CommandRisk:
    """命令风险评估输出。"""

    risk_level: str  # low|medium|high
    reason: str


def _argv_join(argv: List[str]) -> str:
    """将 argv 拼为可读字符串（仅用于规则匹配与 reason 生成，不用于执行）。"""

    return " ".join(argv)


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

    cmd0 = argv[0]
    s = _argv_join(argv)

    # sudo：默认高危（可能突破权限边界）
    if cmd0 == "sudo":
        return CommandRisk(risk_level="high", reason="检测到 sudo")

    # rm -rf / 或 rm -rf ~
    if cmd0.endswith("rm") or cmd0 == "rm":
        joined = " ".join(argv[1:])
        if "-rf" in argv or "-fr" in argv or " -rf " in f" {joined} ":
            if " /" in f" {joined} " or joined.strip() == "/" or joined.strip().startswith("/"):
                return CommandRisk(risk_level="high", reason="检测到 rm -rf 可能删除根目录")
            if "~" in argv or " ~" in f" {joined} ":
                return CommandRisk(risk_level="high", reason="检测到 rm -rf 可能删除 home 目录")

    # 可能破坏系统的数据盘/文件系统操作
    dangerous_prefixes = ("mkfs", "dd", "shutdown", "reboot", "halt")
    if any(cmd0.endswith(p) or cmd0 == p for p in dangerous_prefixes):
        return CommandRisk(risk_level="high", reason=f"检测到高危命令：{cmd0}")

    # 默认：低风险（Phase 2 不做复杂白名单/解析）
    return CommandRisk(risk_level="low", reason="未命中高危模式（Phase 2 最小规则）")
