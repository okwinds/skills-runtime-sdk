"""
PromptManager（模板加载 + 固定注入顺序 + skills list section + history）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/prompt-manager.md`
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from skills_runtime.prompts.history import trim_history
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.skills.models import Skill
from skills_runtime.tools.protocol import ToolSpec


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

    def load(self) -> Tuple[str, str]:
        """
        加载并返回 (system_text, developer_text)。

        规则：
        - 若提供 `*_text`，优先使用；
        - 否则若提供 `*_path`，读取文件；
        - 再否则回退到内置默认文本（保证 SDK 可独立运行）。
        """

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

        return system, developer


class PromptManager:
    """
    Prompt 组装器（Phase 2：chat.completions）。
    """

    def __init__(
        self,
        *,
        templates: PromptTemplates,
        include_skills_list: bool = True,
        history_max_messages: int = 40,
        history_max_chars: int = 120_000,
    ) -> None:
        """
        创建 PromptManager 并固定注入策略。

        参数：
        - `templates`：system/developer 模板来源。
        - `include_skills_list`：是否注入可用 skills 列表（Phase 2 默认开启）。
        - `history_max_messages/history_max_chars`：历史滑窗限制（用于控制上下文长度）。
        """

        self._templates = templates
        self._include_skills_list = include_skills_list
        self._history_max_messages = history_max_messages
        self._history_max_chars = history_max_chars

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

        system_t, developer_t = self._templates.load()
        variables = {
            "task": task,
            "cwd": cwd,
            "tools": "\n".join(f"- {t.name}" for t in tools),
            "skills": "\n".join(
                f"- $[{s.namespace}].{s.skill_name}: {s.description}"
                for s in skills_manager.list_skills(enabled_only=True)
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
            for s in skills_manager.list_skills(enabled_only=True):
                skills_lines.append(f"- $[{s.namespace}].{s.skill_name}: {s.description}")
            messages.append({"role": "user", "content": "\n".join(skills_lines)})

        for skill, source, mention_text in injected_skills:
            content = skills_manager.render_injected_skill(skill, source=source, mention_text=mention_text)
            messages.append({"role": "user", "content": content})

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
            "templates": [{"name": self._templates.name, "version": self._templates.version}],
            "skills_count": len(skills_manager.list_skills(enabled_only=True)),
            "tools_count": len(list(tools)),
            "history_kept": len(kept_history),
            "history_dropped": dropped,
        }
        return messages, debug
