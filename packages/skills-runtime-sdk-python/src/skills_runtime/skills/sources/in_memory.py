from __future__ import annotations

from typing import Any, Dict, List, Mapping

from skills_runtime.config.loader import AgentSdkSkillsConfig
from skills_runtime.core.errors import FrameworkIssue
from skills_runtime.skills.mentions import is_valid_skill_name_slug
from skills_runtime.skills.models import Skill


def scan_in_memory_source(
    *,
    in_memory_registry: Mapping[str, List[Dict[str, Any]]],
    space: AgentSdkSkillsConfig.Space,
    source: AgentSdkSkillsConfig.Source,
    sink: List[Skill],
    errors: List[FrameworkIssue],
) -> None:
    """Scan in-memory source."""

    namespace = source.options.get("namespace")
    if not isinstance(namespace, str) or not namespace.strip():
        errors.append(
            FrameworkIssue(
                code="SKILL_SCAN_METADATA_INVALID",
                message="In-memory source namespace is required.",
                details={"source_id": source.id},
            )
        )
        return

    rows = list(in_memory_registry.get(namespace, []))
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="In-memory skill metadata must be an object.",
                    details={"source_id": source.id, "index": idx},
                )
            )
            continue

        skill_name = row.get("skill_name")
        desc = row.get("description")
        body_loader = row.get("body_loader")
        body_value = row.get("body")
        if not isinstance(skill_name, str) or not skill_name:
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "index": idx, "field": "skill_name"},
                )
            )
            continue
        if not is_valid_skill_name_slug(skill_name):
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={
                        "source_id": source.id,
                        "index": idx,
                        "field": "skill_name",
                        "actual": skill_name,
                        "reason": "invalid_skill_name_slug",
                    },
                )
            )
            continue
        if not isinstance(desc, str) or not desc:
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "index": idx, "field": "description"},
                )
            )
            continue

        if body_loader is None:
            if isinstance(body_value, str):
                body_loader = lambda v=body_value: v
            else:
                errors.append(
                    FrameworkIssue(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "index": idx, "field": "body/body_loader"},
                    )
                )
                continue

        if not callable(body_loader):
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "index": idx, "field": "body_loader"},
                )
            )
            continue

        locator = row.get("locator")
        if not isinstance(locator, str) or not locator:
            locator = f"mem://{namespace}/{skill_name}"

        body_size = row.get("body_size")
        if body_size is not None and not isinstance(body_size, int):
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "index": idx, "field": "body_size"},
                )
            )
            continue

        sink.append(
            Skill(
                space_id=space.id,
                source_id=source.id,
                namespace=space.namespace,
                skill_name=skill_name,
                description=desc,
                locator=locator,
                path=None,
                body_size=body_size,
                body_loader=body_loader,
                required_env_vars=list(row.get("required_env_vars") or []),
                metadata={k: v for k, v in row.items() if k not in {"skill_name", "description", "body", "body_loader"}},
                scope="in-memory",
            )
        )

