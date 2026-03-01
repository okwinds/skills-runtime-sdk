"""
Skills config validation and zero-I/O preflight.

These helpers are implemented as pure functions so they can be tested and reused
without needing a SkillsManager instance.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple
import re

from skills_runtime.config.loader import AgentSdkSkillsConfig
from skills_runtime.core.errors import FrameworkIssue
from skills_runtime.skills.mentions import is_valid_namespace


def scan_options_from_config(skills_config: AgentSdkSkillsConfig) -> dict[str, int | bool]:
    """Read scan options from config (explicit schema; reject implicit expansion)."""

    scan = skills_config.scan
    return {
        "ignore_dot_entries": bool(scan.ignore_dot_entries),
        "max_depth": int(scan.max_depth),
        "max_dirs_per_root": int(scan.max_dirs_per_root),
        "max_frontmatter_bytes": int(scan.max_frontmatter_bytes),
    }


def build_sources_map(skills_config: AgentSdkSkillsConfig) -> Dict[str, AgentSdkSkillsConfig.Source]:
    """Build source id -> source mapping (normalizing dict sources)."""

    out: Dict[str, AgentSdkSkillsConfig.Source] = {}
    for source in skills_config.sources:
        src = source
        if isinstance(source, dict):
            src = AgentSdkSkillsConfig.Source.model_validate(source)
        out[src.id] = src
    return out


def validate_and_normalize_config(
    skills_config: AgentSdkSkillsConfig,
) -> Tuple[AgentSdkSkillsConfig, List[FrameworkIssue]]:
    """Validate config and return a normalized copy (spaces/sources validated models)."""

    errors: List[FrameworkIssue] = []

    sources_map = build_sources_map(skills_config)
    spaces: List[AgentSdkSkillsConfig.Space] = []
    for space in skills_config.spaces:
        if isinstance(space, dict):
            spaces.append(AgentSdkSkillsConfig.Space.model_validate(space))
        else:
            spaces.append(space)
    normalized = skills_config.model_copy(update={"spaces": spaces, "sources": list(sources_map.values())})

    valid_types = {"filesystem", "in-memory", "redis", "pgsql"}
    for source in normalized.sources:
        if source.type not in valid_types:
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill source type is invalid.",
                    details={"source_id": source.id, "source_type": source.type},
                )
            )

    for space in normalized.spaces:
        if not is_valid_namespace(space.namespace):
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={
                        "field": "skills.spaces[].namespace",
                        "space_id": space.id,
                        "actual": space.namespace,
                        "reason": "invalid_namespace",
                    },
                )
            )
        for source_id in space.sources:
            if source_id not in sources_map:
                errors.append(
                    FrameworkIssue(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Space references an unknown source id.",
                        details={"space_id": space.id, "source_id": source_id},
                    )
                )

    return normalized, errors


_PREFLIGHT_ENV_VAR_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_PREFLIGHT_SUPPORTED_SOURCE_TYPES: Set[str] = {"filesystem", "in-memory", "redis", "pgsql"}


def preflight(skills_config: AgentSdkSkillsConfig) -> List[FrameworkIssue]:
    """
    Zero-I/O static preflight for Skills config.

    Constraints:
    - Must not touch filesystem/redis/pgsql.
    - Must not read environment variable contents (no os.environ access).
    """

    def _issue(*, code: str, message: str, path: str, details: Dict[str, Any] | None = None) -> FrameworkIssue:
        """构造一个标准化 FrameworkIssue（用于 preflight 汇总）。"""
        payload: Dict[str, Any] = {"path": path}
        if details:
            payload.update(details)
        return FrameworkIssue(code=code, message=message, details=payload)

    issues: List[FrameworkIssue] = []
    spaces = list(skills_config.spaces)
    sources = list(skills_config.sources)

    # versioning placeholder warning
    try:
        versioning_enabled = bool(getattr(skills_config.versioning, "enabled", False))
        versioning_strategy = str(getattr(skills_config.versioning, "strategy", "TODO") or "TODO")
    except AttributeError:
        versioning_enabled = False
        versioning_strategy = "TODO"
    if versioning_enabled or versioning_strategy != "TODO":
        issues.append(
            _issue(
                code="SKILL_CONFIG_VERSIONING_IGNORED",
                message="skills.versioning is currently a placeholder and has no runtime effect.",
                path="skills.versioning",
                details={"level": "warning", "enabled": versioning_enabled, "strategy": versioning_strategy},
            )
        )

    try:
        extra = dict(getattr(skills_config.versioning, "model_extra", None) or {})
    except AttributeError:
        extra = {}
    if extra:
        issues.append(
            _issue(
                code="SKILL_CONFIG_VERSIONING_UNKNOWN_KEYS",
                message="Unknown keys under skills.versioning are not supported.",
                path="skills.versioning",
                details={"unknown_keys": sorted(list(extra.keys()))},
            )
        )

    seen_space_ids: Dict[str, int] = {}
    for idx, space in enumerate(spaces):
        if space.id in seen_space_ids:
            issues.append(
                _issue(
                    code="SKILL_CONFIG_DUPLICATE_SPACE_ID",
                    message="Duplicate skills space id found.",
                    path=f"skills.spaces[{idx}].id",
                    details={"space_id": space.id, "first_index": seen_space_ids[space.id]},
                )
            )
        else:
            seen_space_ids[space.id] = idx

    for idx, space in enumerate(spaces):
        if not is_valid_namespace(space.namespace):
            issues.append(
                _issue(
                    code="SKILL_CONFIG_INVALID_SPACE_NAMESPACE",
                    message="Invalid skills space namespace.",
                    path=f"skills.spaces[{idx}].namespace",
                    details={
                        "space_id": space.id,
                        "field": "namespace",
                        "actual": space.namespace,
                        "expected": "namespace: 1..7 segments joined by ':'; each segment lowercase slug 2~64 ([a-z0-9-], start/end with [a-z0-9])",
                    },
                )
            )

    seen_source_ids: Dict[str, int] = {}
    source_id_set: set[str] = set()
    for idx, source in enumerate(sources):
        if source.id in seen_source_ids:
            issues.append(
                _issue(
                    code="SKILL_CONFIG_DUPLICATE_SOURCE_ID",
                    message="Duplicate skills source id found.",
                    path=f"skills.sources[{idx}].id",
                    details={"source_id": source.id, "first_index": seen_source_ids[source.id]},
                )
            )
        else:
            seen_source_ids[source.id] = idx
        source_id_set.add(source.id)

    for sidx, space in enumerate(spaces):
        for ridx, ref in enumerate(space.sources):
            if ref not in source_id_set:
                issues.append(
                    _issue(
                        code="SKILL_CONFIG_SPACE_SOURCE_NOT_FOUND",
                        message="Skills space references an unknown source id.",
                        path=f"skills.spaces[{sidx}].sources[{ridx}]",
                        details={"space_id": space.id, "source_id": ref},
                    )
                )

    for idx, source in enumerate(sources):
        stype = source.type
        if stype not in _PREFLIGHT_SUPPORTED_SOURCE_TYPES:
            issues.append(
                _issue(
                    code="SKILL_CONFIG_UNKNOWN_SOURCE_TYPE",
                    message="Unknown skills source type.",
                    path=f"skills.sources[{idx}].type",
                    details={
                        "source_id": source.id,
                        "actual": stype,
                        "supported": sorted(_PREFLIGHT_SUPPORTED_SOURCE_TYPES),
                    },
                )
            )
            continue

        if not isinstance(source.options, dict):
            issues.append(
                _issue(
                    code="SKILL_CONFIG_INVALID_OPTION",
                    message="Skills source options must be an object.",
                    path=f"skills.sources[{idx}].options",
                    details={"source_id": source.id, "expected": "object", "actual": type(source.options).__name__},
                )
            )
            continue

        def _required_non_empty_str(option_key: str) -> None:
            """校验 source.options 中的必填 non-empty string，并在失败时追加 issue。"""
            value = source.options.get(option_key)
            opt_path = f"skills.sources[{idx}].options.{option_key}"
            if value is None:
                issues.append(
                    _issue(
                        code="SKILL_CONFIG_MISSING_REQUIRED_OPTION",
                        message="Missing required skills source option.",
                        path=opt_path,
                        details={"source_id": source.id, "source_type": stype, "option": option_key},
                    )
                )
                return
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    _issue(
                        code="SKILL_CONFIG_INVALID_OPTION",
                        message="Invalid skills source option.",
                        path=opt_path,
                        details={
                            "source_id": source.id,
                            "source_type": stype,
                            "option": option_key,
                            "expected": "non-empty string",
                            "actual": type(value).__name__,
                        },
                    )
                )

        if stype == "filesystem":
            _required_non_empty_str("root")
        elif stype == "in-memory":
            _required_non_empty_str("namespace")
        elif stype == "redis":
            _required_non_empty_str("dsn_env")
            _required_non_empty_str("key_prefix")
        elif stype == "pgsql":
            _required_non_empty_str("dsn_env")
            _required_non_empty_str("schema")
            _required_non_empty_str("table")

        dsn_env = source.options.get("dsn_env")
        if dsn_env is not None:
            opt_path = f"skills.sources[{idx}].options.dsn_env"
            if isinstance(dsn_env, str) and dsn_env.strip() and not _PREFLIGHT_ENV_VAR_NAME_RE.match(dsn_env):
                issues.append(
                    _issue(
                        code="SKILL_CONFIG_INVALID_ENV_VAR_NAME",
                        message="Invalid environment variable name in skills source option.",
                        path=opt_path,
                        details={
                            "source_id": source.id,
                            "source_type": stype,
                            "option": "dsn_env",
                            "actual": dsn_env,
                            "expected": r"^[A-Z_][A-Z0-9_]*$",
                        },
                    )
                )

    return issues
