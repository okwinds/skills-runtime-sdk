"""
PromptManager（模板加载 + 固定注入顺序 + skills list section + history）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/prompt-manager.md`
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from skills_runtime.prompts.history import trim_history
from skills_runtime.skills.mentions import extract_skill_mentions
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.skills.models import Skill
from skills_runtime.tools.protocol import ToolSpec

PromptProfile = Literal["default_agent", "generation_direct", "structured_transform"]
SkillInjectionMode = Literal["all", "explicit_only", "none"]
SkillRenderMode = Literal["body", "method_only", "summary", "none"]
HistoryMode = Literal["none", "compacted", "full"]
ToolsExposure = Literal["none", "explicit_only", "all"]

_PROFILE_DEFAULTS: Dict[str, Dict[str, object]] = {
    "default_agent": {
        "include_skills_list": True,
        "skill_injection_mode": "all",
        "skill_render": "body",
        "history_mode": "full",
        "tools_exposure": "all",
    },
    "generation_direct": {
        "include_skills_list": False,
        "skill_injection_mode": "explicit_only",
        "skill_render": "body",
        "history_mode": "none",
        "tools_exposure": "none",
    },
    "structured_transform": {
        "include_skills_list": False,
        "skill_injection_mode": "explicit_only",
        "skill_render": "summary",
        "history_mode": "none",
        "tools_exposure": "none",
    },
}


def _render_template(template: str, *, variables: Dict[str, str]) -> str:
    """
    轻量模板渲染：替换 `{{var}}`。

    Phase 2：不引入复杂模板引擎，避免额外依赖与不可控行为。
    """

    out = template
    for k, v in variables.items():
        out = out.replace(f"{{{{{k}}}}}", v)
    return out


def _read_text_file(path: Path) -> str:
    """读取 UTF-8 文本文件并返回内容（用于加载 prompt 模板）。"""

    return Path(path).read_text(encoding="utf-8")


def _coerce_choice(value: str, *, allowed: Sequence[str], field_name: str) -> str:
    """校验 profile 策略枚举值，失败时给出可读错误。"""

    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {', '.join(allowed)}")
    return value


@dataclass(frozen=True)
class PromptTemplates:
    """
    Prompt 模板来源（Phase 2）。

    说明：
    - 支持文件路径与直接字符串二选一；若两者都提供，优先使用字符串。
    """

    system_text: Optional[str] = None
    developer_text: Optional[str] = None
    system_path: Optional[Path] = None
    developer_path: Optional[Path] = None
    name: str = "default"
    version: str = "0"

    def __post_init__(self) -> None:
        """初始化实例级模板缓存（frozen dataclass 用 object.__setattr__ 绕过不可变约束）。"""
        object.__setattr__(self, "_loaded_cache", None)

    def load(self) -> Tuple[str, str]:
        """
        加载并返回 (system_text, developer_text)。

        规则：
        - 若提供 `*_text`，优先使用；
        - 否则若提供 `*_path`，读取文件；
        - 再否则回退到内置默认文本（保证 SDK 可独立运行）。
        - 首次加载后缓存到实例，避免同一 run 期间每 turn 重复读文件。
        """

        cached = object.__getattribute__(self, "_loaded_cache")
        if cached is not None:
            return cached

        system = self.system_text
        if system is None and self.system_path is not None:
            system = _read_text_file(self.system_path)
        if system is None:
            system = "You are a general-purpose agent.\n"

        developer = self.developer_text
        if developer is None and self.developer_path is not None:
            developer = _read_text_file(self.developer_path)
        if developer is None:
            developer = "Follow the spec-driven + TDD workflow.\n"

        result = (system, developer)
        object.__setattr__(self, "_loaded_cache", result)
        return result


class PromptManager:
    """
    Prompt 组装器（Phase 2：chat.completions）。
    """

    def __init__(
        self,
        *,
        templates: PromptTemplates,
        profile: PromptProfile = "default_agent",
        include_skills_list: Optional[bool] = None,
        skill_injection_mode: Optional[SkillInjectionMode] = None,
        skill_render: Optional[SkillRenderMode] = None,
        history_mode: Optional[HistoryMode] = None,
        tools_exposure: Optional[ToolsExposure] = None,
        history_max_messages: int = 40,
        history_max_chars: int = 120_000,
    ) -> None:
        """
        创建 PromptManager 并固定注入策略。

        参数：
        - `templates`：system/developer 模板来源。
        - `profile`：prompt profile，决定默认注入策略。
        - `include_skills_list`：是否注入可用 skills 列表；`None` 时使用 profile 默认。
        - `skill_injection_mode/skill_render`：控制 skill 注入范围和渲染形态。
        - `history_mode`：控制是否注入历史。
        - `tools_exposure`：控制传给 provider 的 tools 范围。
        - `history_max_messages/history_max_chars`：历史滑窗限制（用于控制上下文长度）。
        """

        profile_value = _coerce_choice(
            str(profile),
            allowed=("default_agent", "generation_direct", "structured_transform"),
            field_name="profile",
        )
        defaults = _PROFILE_DEFAULTS[profile_value]
        self._templates = templates
        self._profile = profile_value
        self._include_skills_list = (
            bool(defaults["include_skills_list"]) if include_skills_list is None else bool(include_skills_list)
        )
        self._skill_injection_mode = _coerce_choice(
            str(skill_injection_mode or defaults["skill_injection_mode"]),
            allowed=("all", "explicit_only", "none"),
            field_name="skill_injection_mode",
        )
        self._skill_render = _coerce_choice(
            str(skill_render or defaults["skill_render"]),
            allowed=("body", "method_only", "summary", "none"),
            field_name="skill_render",
        )
        self._history_mode = _coerce_choice(
            str(history_mode or defaults["history_mode"]),
            allowed=("none", "compacted", "full"),
            field_name="history_mode",
        )
        self._tools_exposure = _coerce_choice(
            str(tools_exposure or defaults["tools_exposure"]),
            allowed=("none", "explicit_only", "all"),
            field_name="tools_exposure",
        )
        self._history_max_messages = history_max_messages
        self._history_max_chars = history_max_chars

    @property
    def tools_exposure(self) -> str:
        """返回当前 prompt profile 的 tools 暴露策略。"""

        return self._tools_exposure

    def filter_tools_for_task(self, tools: Sequence[ToolSpec], *, task: str, user_input: Optional[str] = None) -> List[ToolSpec]:
        """
        按 `tools_exposure` 过滤 provider tools。

        - `all`：保留全部工具；
        - `none`：返回空列表；
        - `explicit_only`：仅保留当前文本中显式出现的工具名。
        """

        if self._tools_exposure == "all":
            return list(tools)
        if self._tools_exposure == "none":
            return []

        text = "\n".join(part for part in (task, user_input or "") if part)
        return [tool for tool in tools if re.search(rf"(?<![\w.-]){re.escape(tool.name)}(?![\w.-])", text)]

    def _load_template_texts(self) -> Tuple[str, str]:
        """
        加载模板，并应用非默认 profile 的 developer policy 空默认值。

        `PromptTemplates.load()` 的兼容默认会补通用 developer policy；direct/structured profile
        在未显式提供 developer 模板时必须保持空 developer policy。
        """

        system_t, developer_t = self._templates.load()
        if (
            self._profile in {"generation_direct", "structured_transform"}
            and self._templates.developer_text is None
            and self._templates.developer_path is None
        ):
            developer_t = ""
        return system_t, developer_t

    def should_inject_skill(self, skill: Skill, mention_text: Optional[str], *, task: str, user_input: Optional[str] = None) -> bool:
        """判断某个 skill 是否应进入 messages。"""

        if self._skill_injection_mode == "none" or self._skill_render == "none":
            return False
        if self._skill_injection_mode == "all":
            return True

        mentioned = {
            (mention.namespace, mention.skill_name)
            for mention in extract_skill_mentions("\n".join(part for part in (task, user_input or "") if part))
        }
        if mention_text:
            return (skill.namespace, skill.skill_name) in mentioned
        return False

    def _render_skill_summary(self, skill: Skill) -> str:
        """渲染不读取正文的 skill metadata 摘要。"""

        return "\n".join(
            [
                "Skill summary:",
                f"- mention: $[{skill.namespace}].{skill.skill_name}",
                f"- description: {skill.description}",
                f"- locator: {skill.locator}",
            ]
        )

    def _render_skill_method_only(self, skill: Skill, raw_content: str) -> str:
        """尽力提取 method/workflow/usage 片段；找不到时降级为 metadata summary。"""

        lines = raw_content.splitlines()
        start_index: Optional[int] = None
        heading_re = re.compile(r"^\s{0,3}#{1,6}\s+(method|workflow|usage|how to|steps)\b", re.IGNORECASE)
        for index, line in enumerate(lines):
            if heading_re.search(line):
                start_index = index
                break
        if start_index is None:
            return self._render_skill_summary(skill)

        selected: List[str] = []
        for line in lines[start_index:]:
            if selected and re.match(r"^\s{0,3}#{1,6}\s+\S", line):
                break
            selected.append(line)
        body = "\n".join(selected).strip()
        if not body:
            return self._render_skill_summary(skill)
        return "\n".join(["<skill_method>", f"<name>{skill.skill_name}</name>", body, "</skill_method>"])

    def _render_injected_skill(
        self,
        *,
        skills_manager: SkillsManager,
        skill: Skill,
        source: str,
        mention_text: Optional[str],
    ) -> Optional[str]:
        """按 `skill_render` 渲染 skill 注入内容。"""

        if self._skill_render == "none":
            return None
        if self._skill_render == "summary":
            return self._render_skill_summary(skill)
        if self._skill_render == "body":
            return skills_manager.render_injected_skill(skill, source=source, mention_text=mention_text)

        try:
            raw = skills_manager.render_injected_skill(skill, source=source, mention_text=mention_text)
        except Exception:
            return self._render_skill_summary(skill)
        return self._render_skill_method_only(skill, raw)

    def build_messages(
        self,
        *,
        task: str,
        cwd: str,
        tools: Sequence[ToolSpec],
        skills_manager: SkillsManager,
        injected_skills: Sequence[Tuple[Skill, str, Optional[str]]],
        history: List[Dict[str, Any]],
        user_input: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        组装 chat.completions 的 messages。

        顺序（固定，Phase 2）：
        1) system template
        2) developer policy（并入 system；chat.completions wire 不支持 developer role）
        3) skills list section（可选）
        4) injected skill bodies
        5) conversation history（滑窗）
        6) current user task / current user input

        返回：
        - messages：list[dict]（role/content）
        - debug_summary：不含密钥的摘要（可用于 prompt_compiled 事件）
        """

        provider_tools = self.filter_tools_for_task(tools, task=task, user_input=user_input)
        system_t, developer_t = self._load_template_texts()
        # 提前获取 skills 列表，避免后续两处引用各自调用 list_skills() 造成冗余扫描。
        enabled_skills = skills_manager.list_skills(enabled_only=True)
        variables = {
            "task": task,
            "cwd": cwd,
            "tools": "\n".join(f"- {t.name}" for t in provider_tools),
            "skills": "\n".join(
                f"- $[{s.namespace}].{s.skill_name}: {s.description}"
                for s in enabled_skills
            ),
            "constraints": "",
        }

        # 兼容性约束：
        # - OpenAI-compatible chat.completions 通常仅支持 role: system/user/assistant/tool
        # - 为避免 provider 400（invalid role: developer），把 developer policy 合并进 system
        system_text = _render_template(system_t, variables=variables).rstrip()
        developer_text = _render_template(developer_t, variables=variables).rstrip()
        merged_system = system_text
        if developer_text:
            merged_system = f"{system_text}\n\n[Developer Policy]\n{developer_text}\n"
        system_msg = {"role": "system", "content": merged_system}

        messages: List[Dict[str, Any]] = [system_msg]

        if self._include_skills_list:
            skills_lines = ["Available skills (mention via $[namespace].skill_name):"]
            for s in enabled_skills:
                skills_lines.append(f"- $[{s.namespace}].{s.skill_name}: {s.description}")
            messages.append({"role": "user", "content": "\n".join(skills_lines)})

        injected_count = 0
        for skill, source, mention_text in injected_skills:
            if not self.should_inject_skill(skill, mention_text, task=task, user_input=user_input):
                continue
            content = self._render_injected_skill(
                skills_manager=skills_manager,
                skill=skill,
                source=source,
                mention_text=mention_text,
            )
            if content is None:
                continue
            messages.append({"role": "user", "content": content})
            injected_count += 1

        if self._history_mode == "none":
            kept_history: List[Dict[str, Any]] = []
            dropped = len(history)
        else:
            kept_history, dropped = trim_history(
                history,
                max_messages=self._history_max_messages,
                max_chars=self._history_max_chars,
            )
        messages.extend(kept_history)

        if user_input:
            messages.append({"role": "user", "content": user_input})
        else:
            messages.append({"role": "user", "content": task})

        debug = {
            "profile": self._profile,
            "templates": [{"name": self._templates.name, "version": self._templates.version}],
            "skills_count": len(enabled_skills),
            "injected_skills_count": injected_count,
            "skill_injection_mode": self._skill_injection_mode,
            "skill_render": self._skill_render,
            "tools_count": len(provider_tools),
            "tools_exposure": self._tools_exposure,
            "history_mode": self._history_mode,
            "history_kept": len(kept_history),
            "history_dropped": dropped,
        }
        return messages, debug
