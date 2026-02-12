"""
Skills mention 解析（V2）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/skills.md` §3
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List

_V2_MENTION_RE = re.compile(
    r"\$\[(?P<account>[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])):"
    r"(?P<domain>[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9]))\]\."
    r"(?P<skill_name>[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9]))"
)

_ACCOUNT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$")
_DOMAIN_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
_SKILL_NAME_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}[a-z0-9]$")


@dataclass(frozen=True)
class SkillMention:
    """一次合法 V2 mention。"""

    account: str
    domain: str
    skill_name: str
    mention_text: str


def extract_skill_mentions(text: str) -> List[SkillMention]:
    """
    从自由文本中提取 Skills V2 mentions（容错模式：只提取合法片段，不因“疑似但不合法”而报错）。

    约束对齐：
    - `docs/specs/skills-runtime-sdk/docs/skills.md` §3.1（自由文本提取：只提取合法 V2 mention，其它按普通文本处理）

    说明：
    - 该函数用于“自由文本提取”，不得抛出 `SKILL_MENTION_FORMAT_INVALID` 之类的格式错误；
    - 若某些接口需要“一个且仅一个完整 token”，应在上层做严格校验（例如 tool 参数校验）。
    """

    if not text:
        return []

    mentions: List[SkillMention] = []
    for m in _V2_MENTION_RE.finditer(text):
        # 对明显 typo 做容错：形如 `$[a:b].skill]...`，更可能是粘贴/括号错误。
        # 该形态若仍提取出 `$[a:b].skill`，可能导致“误注入/误执行”，因此这里直接忽略。
        if m.end() < len(text) and text[m.end()] == "]":
            continue

        mentions.append(
            SkillMention(
                account=m.group("account"),
                domain=m.group("domain"),
                skill_name=m.group("skill_name"),
                mention_text=m.group(0),
            )
        )

    ordered: List[SkillMention] = []
    seen = set()
    for it in mentions:
        key = (it.account, it.domain, it.skill_name)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(it)
    return ordered


def is_valid_account_slug(value: str) -> bool:
    """
    校验 account slug（用于 skills.spaces[].account 与 mention 解析结果）。

    约束对齐：`docs/specs/skills-runtime-sdk/docs/skills.md` §3.2
    """

    return isinstance(value, str) and bool(_ACCOUNT_SLUG_RE.match(value))


def is_valid_domain_slug(value: str) -> bool:
    """
    校验 domain slug（用于 skills.spaces[].domain 与 mention 解析结果）。

    约束对齐：`docs/specs/skills-runtime-sdk/docs/skills.md` §3.2
    """

    return isinstance(value, str) and bool(_DOMAIN_SLUG_RE.match(value))


def is_valid_skill_name_slug(value: str) -> bool:
    """
    校验 skill_name slug（用于扫描到的 SkillMetadata 与 mention 解析结果）。

    约束对齐：`docs/specs/skills-runtime-sdk/docs/skills.md` §3.2
    """

    return isinstance(value, str) and bool(_SKILL_NAME_SLUG_RE.match(value))
