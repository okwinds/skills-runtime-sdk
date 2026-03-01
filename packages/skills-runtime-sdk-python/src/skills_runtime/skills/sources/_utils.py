from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Mapping, Optional
import re

from skills_runtime.config.loader import AgentSdkSkillsConfig
from skills_runtime.core.errors import FrameworkError, FrameworkIssue


def source_dsn_from_env(source: AgentSdkSkillsConfig.Source) -> str:
    """Read a source DSN from env (fail-closed with structured FrameworkError)."""

    dsn_env = source.options.get("dsn_env")
    if not isinstance(dsn_env, str) or not dsn_env:
        raise FrameworkError(
            code="SKILL_SCAN_METADATA_INVALID",
            message="Skill source dsn_env is required.",
            details={"source_id": source.id, "source_type": source.type, "field": "dsn_env"},
        )

    dsn = os.environ.get(dsn_env)
    if not dsn:
        raise FrameworkError(
            code="SKILL_SCAN_SOURCE_UNAVAILABLE",
            message="Skill source is unavailable in current runtime.",
            details={
                "source_id": source.id,
                "source_type": source.type,
                "dsn_env": dsn_env,
                "env_present": False,
            },
        )
    return dsn


def parse_json_string_field(value: Any, *, field: str, source_id: str, locator: str) -> Any:
    """Parse metadata fields encoded as JSON strings."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise FrameworkError(
            code="SKILL_SCAN_METADATA_INVALID",
            message="Skill metadata is invalid.",
            details={"source_id": source_id, "locator": locator, "field": field},
        )
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise FrameworkError(
            code="SKILL_SCAN_METADATA_INVALID",
            message="Skill metadata is invalid.",
            details={"source_id": source_id, "locator": locator, "field": field, "reason": str(exc)},
        ) from exc


def ensure_metadata_string(value: Any, *, field: str, source_id: str, locator: str) -> str:
    """Assert metadata field is a non-empty string."""

    if not isinstance(value, str) or not value:
        raise FrameworkError(
            code="SKILL_SCAN_METADATA_INVALID",
            message="Skill metadata is invalid.",
            details={"source_id": source_id, "locator": locator, "field": field},
        )
    return value


def normalize_optional_int(value: Any, *, field: str, source_id: str, locator: str) -> Optional[int]:
    """Normalize optional integer fields to int/None."""

    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise FrameworkError(
        code="SKILL_SCAN_METADATA_INVALID",
        message="Skill metadata is invalid.",
        details={"source_id": source_id, "locator": locator, "field": field},
    )


def safe_identifier(raw: Any, *, field: str, source_id: str) -> str:
    """Validate SQL identifiers (schema/table) are safe."""

    if not isinstance(raw, str) or not raw:
        raise FrameworkError(
            code="SKILL_SCAN_METADATA_INVALID",
            message="Skill metadata is invalid.",
            details={"source_id": source_id, "field": field},
        )
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", raw):
        raise FrameworkError(
            code="SKILL_SCAN_METADATA_INVALID",
            message="Skill metadata is invalid.",
            details={"source_id": source_id, "field": field},
        )
    return raw


def scan_unavailable_source(*, source: AgentSdkSkillsConfig.Source, errors: List[FrameworkIssue]) -> None:
    """Record a structured error for an unavailable source in current runtime."""

    required_env = source.options.get("dsn_env")
    details: Dict[str, Any] = {"source_id": source.id, "source_type": source.type}
    if isinstance(required_env, str) and required_env:
        details["dsn_env"] = required_env
        details["env_present"] = bool(os.environ.get(required_env))
    errors.append(
        FrameworkIssue(
            code="SKILL_SCAN_SOURCE_UNAVAILABLE",
            message="Skill source is unavailable in current runtime.",
            details=details,
        )
    )


def utc_rfc3339_now() -> str:
    """Generate UTC RFC3339 timestamp."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_from_timestamp_rfc3339(ts: float) -> str:
    """Convert UNIX timestamp to UTC RFC3339 string."""

    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")

