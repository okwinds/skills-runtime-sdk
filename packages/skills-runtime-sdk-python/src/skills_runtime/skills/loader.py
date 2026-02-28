"""
Skill loader（解析 SKILL.md frontmatter + agents/openai.yaml）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/skills.md`
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from skills_runtime.skills.models import Skill


@dataclass(frozen=True)
class SkillLoadError(Exception):
    """Skill 加载错误（用于控制流与错误聚合）。"""

    message: str
    path: Path

    def __str__(self) -> str:  # pragma: no cover
        """返回用于日志/UI 展示的错误信息（包含路径）。"""

        return f"{self.message} ({self.path})"


@dataclass(frozen=True)
class SkillMetadata:
    """
    Skill 元数据（metadata-only 扫描阶段产物）。

    说明：
    - filesystem scan 阶段必须 frontmatter-only：不得读取正文 body。
    - 该结构用于 SkillsManager 在 scan 阶段构建索引。
    """

    skill_name: str
    description: str
    required_env_vars: List[str]
    metadata: Dict[str, Any]
    scope: str | None = None


def _collapse_whitespace(s: str) -> str:
    """把字符串中的多余空白折叠为单个空格（用于规范化 description 等字段）。"""

    return " ".join(str(s).split())


def _split_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """
    将 SKILL.md 拆分为 frontmatter 与 body。

    约定：
    - frontmatter 必须以 `---` 开始并以第二个 `---` 结束
    - 若不满足：视为无 frontmatter（返回空 dict + 原文 body）
    """

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text

    fm_lines: List[str] = []
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
        fm_lines.append(lines[i])
    if end_idx is None:
        return {}, text

    fm_text = "".join(fm_lines)
    body = "".join(lines[end_idx + 1 :])
    try:
        obj = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    return obj, body


def _load_required_env_vars(skill_dir: Path) -> List[str]:
    """
    从 `<skill_dir>/agents/openai.yaml` 解析 env_var dependencies（fail-open）。
    """

    p = skill_dir / "agents" / "openai.yaml"
    if not p.exists():
        return []
    try:
        obj = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(obj, dict):
        return []

    deps = obj.get("dependencies") or {}
    if not isinstance(deps, dict):
        return []
    tools = deps.get("tools") or []
    if not isinstance(tools, list):
        return []

    out: List[str] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "env_var":
            continue
        value = item.get("value")
        if isinstance(value, str) and value:
            out.append(value)
    # 去重 + 保序
    seen = set()
    uniq: List[str] = []
    for v in out:
        if v in seen:
            continue
        seen.add(v)
        uniq.append(v)
    return uniq


def _read_frontmatter_only(path: Path, *, max_frontmatter_bytes: int) -> Dict[str, Any]:
    """
    以流式方式读取 YAML frontmatter（只读到第 2 个 `---`，不读取正文）。

    参数：
    - path：指向 `SKILL.md`
    - max_frontmatter_bytes：frontmatter 最大字节数上限（含边界行），用于防御异常大文件与强制 metadata-only

    返回：
    - frontmatter dict（fail-open：YAML 解析失败返回空 dict）

    异常：
    - SkillLoadError：当 frontmatter 缺失/未闭合/过大
    """

    p = Path(path).resolve()
    if not p.exists() or not p.is_file():
        raise SkillLoadError("SKILL.md 不存在或不是文件", p)

    if not isinstance(max_frontmatter_bytes, int) or max_frontmatter_bytes < 1:
        # 防御：配置错误时仍保证不会读到正文
        max_frontmatter_bytes = 1

    bytes_read = 0
    fm_lines: List[str] = []

    with p.open("r", encoding="utf-8") as f:
        first = f.readline()
        if not first:
            raise SkillLoadError("frontmatter_missing", p)

        bytes_read += len(first.encode("utf-8"))
        if bytes_read > max_frontmatter_bytes:
            raise SkillLoadError("frontmatter_too_large", p)

        if first.strip() != "---":
            raise SkillLoadError("frontmatter_missing", p)

        while True:
            line = f.readline()
            if line == "":
                raise SkillLoadError("frontmatter_unterminated", p)

            bytes_read += len(line.encode("utf-8"))
            if bytes_read > max_frontmatter_bytes:
                raise SkillLoadError("frontmatter_too_large", p)

            if line.strip() == "---":
                break
            fm_lines.append(line)

    fm_text = "".join(fm_lines)
    try:
        obj = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    return obj


def load_skill_metadata_from_path(
    path: Path,
    *,
    scope: str | None = None,
    max_frontmatter_bytes: int = 65536,
) -> SkillMetadata:
    """
    从 SKILL.md 加载 metadata-only 信息（frontmatter + agents/openai.yaml env_var deps）。

    约束：
    - 不读取正文 body（不得一次性读取全文）
    - frontmatter 必须闭合（第 1 行/第 2 个 `---`）

    参数：
    - path：指向某个 `SKILL.md`
    - scope：可选（repo/user/system）
    - max_frontmatter_bytes：frontmatter 最大字节数上限（默认 64KiB）
    """

    p = Path(path).resolve()
    if p.name != "SKILL.md":
        raise SkillLoadError("文件名必须为 SKILL.md", p)

    fm = _read_frontmatter_only(p, max_frontmatter_bytes=max_frontmatter_bytes)

    name = fm.get("name")
    desc = fm.get("description")
    if not isinstance(name, str) or not name:
        raise SkillLoadError("frontmatter 缺少必填字段 name", p)
    if not isinstance(desc, str) or not desc:
        raise SkillLoadError("frontmatter 缺少必填字段 description", p)

    name = name.strip()
    if not name or any(c.isspace() for c in name):
        raise SkillLoadError("frontmatter 字段 name 非法（不得包含空白）", p)

    desc = _collapse_whitespace(desc)

    required_env_vars = _load_required_env_vars(p.parent)
    metadata = dict(fm)
    metadata.pop("name", None)
    metadata.pop("description", None)

    md = metadata.get("metadata")
    if isinstance(md, dict):
        sd = md.get("short-description")
        if isinstance(sd, str) and sd:
            md = dict(md)
            md["short-description"] = _collapse_whitespace(sd)
            metadata["metadata"] = md

    return SkillMetadata(
        skill_name=name,
        description=desc,
        required_env_vars=required_env_vars,
        metadata=metadata,
        scope=scope,
    )


def load_skill_from_path(path: Path, *, scope: str | None = None) -> Skill:
    """
    从 SKILL.md 路径加载 Skill。

    参数：
    - path：指向某个 `SKILL.md`
    - scope：可选（repo/user/system）
    """

    p = Path(path).resolve()
    if p.name != "SKILL.md":
        raise SkillLoadError("文件名必须为 SKILL.md", p)
    if not p.exists() or not p.is_file():
        raise SkillLoadError("SKILL.md 不存在或不是文件", p)

    raw = p.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(raw)

    name = fm.get("name")
    desc = fm.get("description")
    if not isinstance(name, str) or not name:
        raise SkillLoadError("frontmatter 缺少必填字段 name", p)
    if not isinstance(desc, str) or not desc:
        raise SkillLoadError("frontmatter 缺少必填字段 description", p)

    name = name.strip()
    if not name or any(c.isspace() for c in name):
        raise SkillLoadError("frontmatter 字段 name 非法（不得包含空白）", p)

    desc = _collapse_whitespace(desc)

    required_env_vars = _load_required_env_vars(p.parent)
    metadata = dict(fm)
    metadata.pop("name", None)
    metadata.pop("description", None)

    md = metadata.get("metadata")
    if isinstance(md, dict):
        sd = md.get("short-description")
        if isinstance(sd, str) and sd:
            md = dict(md)
            md["short-description"] = _collapse_whitespace(sd)
            metadata["metadata"] = md

    return Skill(
        space_id="",
        source_id="",
        namespace="",
        skill_name=name,
        description=desc,
        locator=str(p),
        path=p,
        body_size=len(raw.encode("utf-8")),
        body_loader=lambda: raw,
        required_env_vars=required_env_vars,
        metadata={"body_markdown": body, **metadata},
        scope=scope,
    )
