"""
Compaction prompts（/compact）与对话历史压缩辅助函数。

目标：
- 为内部生产提供可复用、可回归的“上下文压缩/交接摘要（handoff summary）”能力；
- 默认使用中文提示词，强调不泄露 secrets 与给出可继续执行的结构化信息。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


COMPACTION_SYSTEM_PROMPT_ZH = """你是一个“对话压缩器（Conversation Compactor）”。

你的任务是把给定的对话记录压缩成一段可用于“继续工作”的 handoff 摘要。

硬性约束：
- 不要输出任何密钥、token、密码、私钥等敏感信息；若对话中出现，请用 <redacted> 替代。
- 不要编造不存在的事实；不确定的内容明确标注“不确定/待确认”。
- 输出必须结构化、可执行，方便另一个 agent/人类接手继续推进。
"""


COMPACTION_USER_PROMPT_TEMPLATE_ZH = """请根据下面的“任务描述”和“对话节选”，生成一段 handoff 摘要。

任务描述：
{task}

对话节选（可能不完整；请以可见内容为准）：
{transcript}

输出格式（Markdown）：
1) 目标/范围（Goal/Scope）
2) 已完成进展（Progress）
3) 关键决策与理由（Key Decisions）
4) 当前状态/阻塞点（Current State / Blockers）
5) 下一步建议（Next Steps）
6) 风险与注意事项（Risks / Notes）

再次提醒：不要泄露 secrets；遇到疑似敏感值用 <redacted>。"""


SUMMARY_PREFIX_TEMPLATE_ZH = """[对话压缩摘要｜handoff]
说明：这是一次上下文压缩生成的摘要，用于继续推进任务；可能遗漏细节。
"""


def _clip_text_middle(text: str, *, max_chars: int) -> str:
    """
    将文本裁剪到不超过 max_chars，并尽量保留首尾两端（中间用省略号替代）。

    参数：
    - text：原始文本
    - max_chars：最大字符数
    """

    s = str(text or "")
    if len(s) <= int(max_chars):
        return s
    if max_chars <= 50:
        return s[: max_chars - 3] + "..."
    head = max_chars // 3
    tail = max_chars - head - 5
    return s[:head] + "\n...\n" + s[-tail:]


def format_history_for_compaction(
    history: List[Dict[str, Any]],
    *,
    max_chars: int,
    keep_last_messages: int,
) -> str:
    """
    将 Agent 内部 history 格式化为“对话节选”文本（用于 compaction turn 输入）。

    参数：
    - history：Agent 运行时维护的历史消息（role/content/tool_calls/tool 等）
    - max_chars：返回字符串的最大长度（用于降低 compaction turn 的上下文压力）
    - keep_last_messages：优先保留末尾 N 条 user/assistant 的原文消息（工具输出只做摘要）
    """

    kept: List[Dict[str, Any]] = []
    ua = [m for m in history if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    tail = ua[-int(keep_last_messages) :] if keep_last_messages > 0 else []
    tail_ids = {id(x) for x in tail}

    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role in ("user", "assistant"):
            if id(m) in tail_ids:
                kept.append(m)
            continue
        if role == "tool":
            kept.append(m)

    lines: List[str] = []
    for m in kept:
        role = str(m.get("role") or "")
        if role in ("user", "assistant"):
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                lines.append(f"{role.upper()}:\n{content.strip()}")
            continue
        if role == "tool":
            tool_call_id = str(m.get("tool_call_id") or "")
            raw = m.get("content")
            if not isinstance(raw, str) or not raw.strip():
                continue
            ok: Optional[bool] = None
            error_kind: Optional[str] = None
            stdout: str = ""
            stderr: str = ""
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    ok = obj.get("ok") if isinstance(obj.get("ok"), bool) else None
                    error_kind = obj.get("error_kind") if isinstance(obj.get("error_kind"), str) else None
                    stdout = str(obj.get("stdout") or "")
                    stderr = str(obj.get("stderr") or "")
            except json.JSONDecodeError:
                pass
            head = f"TOOL(tool_call_id={tool_call_id}, ok={ok}, error_kind={error_kind})"
            body = []
            if stdout.strip():
                body.append("stdout:\n" + _clip_text_middle(stdout.strip(), max_chars=800))
            if stderr.strip():
                body.append("stderr:\n" + _clip_text_middle(stderr.strip(), max_chars=800))
            if not body:
                body.append(_clip_text_middle(raw.strip(), max_chars=800))
            lines.append(head + "\n" + "\n".join(body))

    transcript = "\n\n---\n\n".join(lines).strip()
    return _clip_text_middle(transcript, max_chars=int(max_chars))


def build_compaction_messages(*, task: str, transcript: str) -> List[Dict[str, Any]]:
    """
    构造 compaction turn 的 chat.completions messages（tools 禁用）。

    参数：
    - task：当前 run 的任务描述
    - transcript：格式化后的对话节选文本
    """

    user = COMPACTION_USER_PROMPT_TEMPLATE_ZH.format(task=str(task or "").strip(), transcript=str(transcript or "").strip())
    return [
        {"role": "system", "content": COMPACTION_SYSTEM_PROMPT_ZH.strip()},
        {"role": "user", "content": user.strip()},
    ]

