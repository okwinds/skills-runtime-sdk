"""
Skills env var 注入与缺失处理（从 core.agent_loop 拆出）。

约束：
- 不得把真实 env value 写入 events/WAL；
- Web 场景通过 human_request 驱动 UI 弹窗，避免 human_response 落盘 value。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Callable, Dict, Optional

from skills_runtime.config.loader import AgentSdkConfig
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.run_errors import MissingRequiredEnvVarError
from skills_runtime.core.utils import now_rfc3339
from skills_runtime.skills.models import Skill
from skills_runtime.tools.protocol import HumanIOProvider


async def ensure_skill_env_vars(
    skill: Skill,
    *,
    config: AgentSdkConfig,
    env_store: Dict[str, str],
    human_io: Optional[HumanIOProvider],
    run_id: str,
    turn_id: str,
    emit: Callable[[AgentEvent], Any],
) -> bool:
    """
    确保某个 skill 的 env_var 依赖已满足（session-only，不落盘值）。

    行为对齐：
    - `docs/specs/skills-runtime-sdk/docs/env-store.md`
    - `docs/specs/skills-runtime-sdk/docs/skills.md` §6.3
    """

    required = list(getattr(skill, "required_env_vars", []) or [])
    if not required:
        return True

    raw_policy = str(getattr(config.skills, "env_var_missing_policy", "ask_human") or "ask_human").strip().lower()
    policy = raw_policy if raw_policy in ("fail_fast", "ask_human", "skip_skill") else "ask_human"

    for env_name in required:
        env_name = str(env_name or "").strip()
        if not env_name:
            continue

        # 1) session env_store 优先
        if env_name in env_store and str(env_store.get(env_name) or "") != "":
            emit(
                AgentEvent(
                    type="env_var_set",
                    timestamp=now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                    payload={
                        "env_var": env_name,
                        "skill_name": skill.skill_name,
                        "skill_path": str(skill.path or skill.locator),
                        "value_source": "provided",
                    },
                )
            )
            continue

        # 2) process env 次之（允许 CLI/CI）
        pv = os.environ.get(env_name, "")
        if pv:
            env_store[env_name] = pv
            emit(
                AgentEvent(
                    type="env_var_set",
                    timestamp=now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                    payload={
                        "env_var": env_name,
                        "skill_name": skill.skill_name,
                        "skill_path": str(skill.path or skill.locator),
                        "value_source": "process_env",
                    },
                )
            )
            continue

        # 3) 缺失：需要 human 收集
        emit(
            AgentEvent(
                type="env_var_required",
                timestamp=now_rfc3339(),
                run_id=run_id,
                turn_id=turn_id,
                payload={
                    "env_var": env_name,
                    "skill_name": skill.skill_name,
                    "skill_path": str(skill.path or skill.locator),
                    "source": "skill_dependency",
                    "policy": policy,
                },
            )
        )

        if policy == "skip_skill":
            emit(
                AgentEvent(
                    type="skill_injection_skipped",
                    timestamp=now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                    payload={
                        "skill_name": skill.skill_name,
                        "skill_path": str(skill.path or skill.locator),
                        "reason": "missing_env_var",
                        "missing_env_vars": [env_name],
                        "policy": policy,
                    },
                )
            )
            return False

        if policy == "fail_fast":
            raise MissingRequiredEnvVarError(
                missing_env_vars=[env_name],
                skill_name=skill.skill_name,
                skill_path=str(skill.path or skill.locator),
                policy=policy,
            )

        if human_io is None:
            raise ValueError(f"missing required env var (no HumanIOProvider): {env_name}")

        call_id = f"env_{env_name}_{uuid.uuid4().hex}"
        question = f"请提供环境变量 {env_name} 的值（仅 session 内存使用，不落盘）。"

        # 用 human_request 事件驱动 UI 弹窗；不发送 human_response（避免落盘 value）
        emit(
            AgentEvent(
                type="human_request",
                timestamp=now_rfc3339(),
                run_id=run_id,
                turn_id=turn_id,
                payload={
                    "call_id": call_id,
                    "question": question,
                    "choices": None,
                    "context": {
                        "kind": "env_var",
                        "env_var": env_name,
                        "skill": {"name": skill.skill_name, "path": str(skill.path or skill.locator)},
                    },
                },
            )
        )

        answer = await asyncio.to_thread(
            human_io.request_human_input,
            call_id=call_id,
            question=question,
            choices=None,
            context={
                "kind": "env_var",
                "env_var": env_name,
                "skill": {"name": skill.skill_name, "path": str(skill.path or skill.locator)},
            },
            timeout_ms=config.run.human_timeout_ms,
        )
        if not isinstance(answer, str) or answer == "":
            raise ValueError(f"missing required env var: {env_name}")

        env_store[env_name] = answer
        emit(
            AgentEvent(
                type="env_var_set",
                timestamp=now_rfc3339(),
                run_id=run_id,
                turn_id=turn_id,
                payload={
                    "env_var": env_name,
                    "skill_name": skill.skill_name,
                    "skill_path": str(skill.path or skill.locator),
                    "value_source": "human",
                },
            )
        )

    return True


__all__ = ["ensure_skill_env_vars"]

