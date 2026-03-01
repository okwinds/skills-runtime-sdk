from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class SkillScaffoldResult:
    """
    Skill 脚手架创建结果。

    字段：
    - skill_dir：skill 目录路径（包含 `SKILL.md`）
    - skill_md：写入的 `SKILL.md` 文件路径
    """

    skill_dir: Path
    skill_md: Path


def validate_skill_name(skill_name: str) -> None:
    """
    校验 skill_name 是否为可移植的 slug（用于目录名与 frontmatter.name）。

    约束：
    - 仅允许小写字母/数字/`_`/`-`，且必须以字母或数字开头；
    - 目的：避免路径穿越与不可复刻的命名（空格/中文/特殊字符在不同环境差异大）。
    """

    if not isinstance(skill_name, str) or not skill_name.strip():
        raise ValueError("skill_name 不能为空")
    if not _SKILL_NAME_RE.match(skill_name):
        raise ValueError("skill_name 必须匹配 ^[a-z0-9][a-z0-9_-]*$")


def render_skill_markdown(
    *,
    skill_name: str,
    description: str,
    title: str | None,
    body_markdown: str,
) -> str:
    """
    渲染符合 SkillsManager 解析规则的 `SKILL.md` 文本。

    参数：
    - skill_name：技能名（slug）
    - description：一句话描述
    - title：可选标题（用于 metadata.short-description 与 H1）
    - body_markdown：正文（Markdown，可为空）
    """

    validate_skill_name(skill_name)
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description 不能为空")

    safe_title = (title or "").strip() or f"Skill：{skill_name}"
    body = (body_markdown or "").strip("\n")

    parts = [
        "---",
        f"name: {skill_name}",
        f'description: "{description.strip()}"',
        "metadata:",
        f'  short-description: "{safe_title}"',
        '  generated-by: "skills-runtime-studio-mvp"',
        "---",
        "",
        f"# {safe_title}",
        "",
    ]
    if body:
        parts.append(body)
        parts.append("")
    return "\n".join(parts)


def write_skill(
    *,
    root_dir: Path,
    skill_name: str,
    description: str,
    title: str | None = None,
    body_markdown: str = "",
    overwrite: bool = False,
) -> SkillScaffoldResult:
    """
    在 `root_dir` 下创建/写入一个 skill（`<root>/<skill_name>/SKILL.md`）。

    参数：
    - root_dir：filesystem source roots 之一（目录）
    - overwrite：默认 false；若 SKILL.md 已存在则报错
    """

    root = Path(root_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    validate_skill_name(skill_name)
    skill_dir = (root / skill_name).resolve()
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = (skill_dir / "SKILL.md").resolve()

    if skill_md.exists() and not overwrite:
        raise FileExistsError(f"SKILL.md already exists: {skill_md}")

    text = render_skill_markdown(
        skill_name=skill_name,
        description=description,
        title=title,
        body_markdown=body_markdown,
    )
    skill_md.write_text(text, encoding="utf-8")
    return SkillScaffoldResult(skill_dir=skill_dir, skill_md=skill_md)
