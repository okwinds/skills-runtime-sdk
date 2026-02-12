"""
对话历史管理：滑动窗口（Phase 2）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/prompt-manager.md` §3.1
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _message_char_len(msg: Dict[str, Any]) -> int:
    """
    估算单条 message 的“字符长度”。

    说明：
    - Phase 2 不做 token 精确计数（避免引入额外 tokenizer 依赖）；
    - 对非字符串 content 回退到 `str(content)` 的长度估算。
    """

    # Phase 2：粗粒度按字符串长度估算（不做 token 精确计数）
    content = msg.get("content")
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    return len(str(content))


def trim_history(
    history: List[Dict[str, Any]],
    *,
    max_messages: int,
    max_chars: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    裁剪对话历史（优先保留最近消息）。

    参数：
    - history：chat messages（list[dict]）
    - max_messages：最多保留条数
    - max_chars：最多保留字符数（粗估）

    返回：
    - kept：保留的历史（按原顺序）
    - dropped：丢弃条数
    """

    if max_messages < 0 or max_chars < 0:
        raise ValueError("max_messages/max_chars 必须 >= 0")

    if not history:
        return [], 0

    # 先按条数裁剪（保留尾部）
    kept = history[-max_messages:] if max_messages > 0 else []
    dropped = len(history) - len(kept)

    # 再按字符数裁剪（从头部丢弃，保留尾部）
    if max_chars == 0:
        return [], len(history)

    total = sum(_message_char_len(m) for m in kept)
    while kept and total > max_chars:
        first = kept.pop(0)
        total -= _message_char_len(first)
        dropped += 1

    return kept, dropped
