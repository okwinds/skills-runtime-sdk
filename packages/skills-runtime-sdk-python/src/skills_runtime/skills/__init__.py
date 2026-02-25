"""
Skills 系统（SKILL.md 扫描、匹配、注入）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/skills.md`
"""

from __future__ import annotations

from skills_runtime.skills.loader import SkillLoadError, load_skill_from_path
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.skills.models import Skill

__all__ = ["Skill", "SkillLoadError", "SkillsManager", "load_skill_from_path"]

