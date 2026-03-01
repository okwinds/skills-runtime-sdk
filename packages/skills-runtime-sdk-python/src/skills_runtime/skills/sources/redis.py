from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, MutableMapping

from skills_runtime.config.loader import AgentSdkSkillsConfig
from skills_runtime.core.errors import FrameworkError, FrameworkIssue
from skills_runtime.skills.bundle_cache import is_sha256_hex
from skills_runtime.skills.bundles import ExtractedBundle, ensure_extracted_bundle
from skills_runtime.skills.mentions import is_valid_skill_name_slug
from skills_runtime.skills.models import Skill
from skills_runtime.skills.sources._utils import ensure_metadata_string, normalize_optional_int, parse_json_string_field


def get_redis_client(
    *,
    source: AgentSdkSkillsConfig.Source,
    source_clients: Mapping[str, Any],
    runtime_source_clients: MutableMapping[str, Any],
    source_dsn_from_env: Callable[[AgentSdkSkillsConfig.Source], str],
) -> Any:
    """Get redis client (prefer injected; else initialize from dsn_env and cache)."""

    injected = source_clients.get(source.id)
    if injected is not None:
        return injected
    cached = runtime_source_clients.get(source.id)
    if cached is not None:
        return cached

    dsn = source_dsn_from_env(source)
    try:
        import redis  # type: ignore[import-not-found]
    except ImportError as exc:
        dsn_env = source.options.get("dsn_env")
        raise FrameworkError(
            code="SKILL_SCAN_SOURCE_UNAVAILABLE",
            message="Skill source is unavailable in current runtime.",
            details={
                "source_id": source.id,
                "source_type": source.type,
                "dsn_env": dsn_env,
                "env_present": True,
                "reason": f"redis dependency unavailable: {exc}",
            },
        ) from exc

    try:
        client = redis.from_url(dsn)
    except Exception as exc:
        dsn_env = source.options.get("dsn_env")
        raise FrameworkError(
            code="SKILL_SCAN_SOURCE_UNAVAILABLE",
            message="Skill source is unavailable in current runtime.",
            details={
                "source_id": source.id,
                "source_type": source.type,
                "dsn_env": dsn_env,
                "env_present": True,
                "reason": f"redis connect failed: {exc}",
            },
        ) from exc
    runtime_source_clients[source.id] = client
    return client


def ensure_redis_bundle_extracted(
    *,
    skill: Skill,
    find_source_by_id: Callable[[str], AgentSdkSkillsConfig.Source],
    get_redis_client_for_source: Callable[[AgentSdkSkillsConfig.Source], Any],
    bundle_cache_root: Path,
    bundle_max_bytes: int,
) -> ExtractedBundle:
    """
    Lazily fetch and extract a skill bundle from Redis.

    Constraints:
    - Must not be called during scan.
    - Cache is content-addressed by bundle_sha256.
    """

    source = find_source_by_id(skill.source_id)
    if str(source.type or "").strip().lower() != "redis":
        raise FrameworkError(
            code="SKILL_BUNDLE_SOURCE_UNSUPPORTED",
            message="Skill bundle is not supported for this source type in current version.",
            details={"source_id": skill.source_id, "source_type": source.type, "locator": skill.locator},
        )

    key_prefix = source.options.get("key_prefix")
    if not isinstance(key_prefix, str) or not key_prefix:
        raise FrameworkError(
            code="SKILL_BUNDLE_CONTRACT_INVALID",
            message="Skill bundle contract is invalid.",
            details={"source_id": source.id, "field": "key_prefix"},
        )

    meta = skill.metadata or {}
    bundle_sha256 = meta.get("bundle_sha256")
    if not isinstance(bundle_sha256, str) or not bundle_sha256:
        raise FrameworkError(
            code="SKILL_BUNDLE_FINGERPRINT_MISSING",
            message="Skill bundle fingerprint is missing in metadata.",
            details={"source_id": source.id, "locator": skill.locator, "field": "bundle_sha256"},
        )
    if not is_sha256_hex(bundle_sha256):
        raise FrameworkError(
            code="SKILL_BUNDLE_CONTRACT_INVALID",
            message="Skill bundle contract is invalid.",
            details={"source_id": source.id, "locator": skill.locator, "field": "bundle_sha256"},
        )

    bundle_format = meta.get("bundle_format", "zip")
    if bundle_format is not None:
        if not isinstance(bundle_format, str) or str(bundle_format).strip().lower() != "zip":
            raise FrameworkError(
                code="SKILL_BUNDLE_CONTRACT_INVALID",
                message="Skill bundle contract is invalid.",
                details={"source_id": source.id, "locator": skill.locator, "field": "bundle_format"},
            )

    bundle_key = meta.get("bundle_key")
    if bundle_key is None:
        bundle_key = f"{key_prefix}bundle:{skill.namespace}:{skill.skill_name}"
    if not isinstance(bundle_key, str) or not bundle_key:
        raise FrameworkError(
            code="SKILL_BUNDLE_CONTRACT_INVALID",
            message="Skill bundle contract is invalid.",
            details={"source_id": source.id, "locator": skill.locator, "field": "bundle_key"},
        )

    # fast path: reuse cache without hitting Redis
    final_dir = (Path(bundle_cache_root) / bundle_sha256).resolve()
    if final_dir.exists() and final_dir.is_dir():
        return ExtractedBundle(bundle_sha256=bundle_sha256, bundle_root=final_dir)

    client = get_redis_client_for_source(source)
    bundle_raw = client.get(bundle_key)
    if bundle_raw is None:
        raise FrameworkError(
            code="SKILL_BUNDLE_NOT_FOUND",
            message="Skill bundle is not found in source store.",
            details={"source_id": source.id, "locator": skill.locator, "bundle_key": bundle_key},
        )
    if isinstance(bundle_raw, bytes):
        bundle_bytes = bundle_raw
    elif isinstance(bundle_raw, bytearray):
        bundle_bytes = bytes(bundle_raw)
    else:
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle bytes are invalid.",
            details={"source_id": source.id, "locator": skill.locator, "bundle_key": bundle_key, "actual_type": type(bundle_raw).__name__},
        )

    return ensure_extracted_bundle(
        cache_root=Path(bundle_cache_root),
        bundle_sha256=bundle_sha256,
        bundle_bytes=bundle_bytes,
        max_bytes=int(bundle_max_bytes),
    )


def scan_redis_source(
    *,
    space: AgentSdkSkillsConfig.Space,
    source: AgentSdkSkillsConfig.Source,
    sink: List[Skill],
    errors: List[FrameworkIssue],
    get_redis_client_for_source: Callable[[AgentSdkSkillsConfig.Source], Any],
) -> None:
    """Scan redis source (metadata-only)."""

    key_prefix = source.options.get("key_prefix")
    if not isinstance(key_prefix, str) or not key_prefix:
        errors.append(
            FrameworkIssue(
                code="SKILL_SCAN_METADATA_INVALID",
                message="Redis source key_prefix is required.",
                details={"source_id": source.id, "field": "key_prefix"},
            )
        )
        return

    try:
        client = get_redis_client_for_source(source)
    except FrameworkError as exc:
        errors.append(exc.to_issue())
        return

    pattern = f"{key_prefix}meta:{space.namespace}:*"
    try:
        keys_iter = client.scan_iter(match=pattern)
    except Exception as exc:
        dsn_env = source.options.get("dsn_env")
        errors.append(
            FrameworkIssue(
                code="SKILL_SCAN_SOURCE_UNAVAILABLE",
                message="Skill source is unavailable in current runtime.",
                details={
                    "source_id": source.id,
                    "source_type": source.type,
                    "dsn_env": dsn_env,
                    "env_present": bool(os.environ.get(dsn_env)) if isinstance(dsn_env, str) else False,
                    "reason": f"redis scan failed: {exc}",
                },
            )
        )
        return

    keys_iter = iter(keys_iter)
    while True:
        try:
            raw_key = next(keys_iter)
        except StopIteration:
            break
        except Exception as exc:
            dsn_env = source.options.get("dsn_env")
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_SOURCE_UNAVAILABLE",
                    message="Skill source is unavailable in current runtime.",
                    details={
                        "source_id": source.id,
                        "source_type": source.type,
                        "dsn_env": dsn_env,
                        "env_present": bool(os.environ.get(dsn_env)) if isinstance(dsn_env, str) else False,
                        "reason": f"redis scan failed: {exc}",
                    },
                )
            )
            break

        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
        locator = f"redis://{key}"
        try:
            meta = client.hgetall(raw_key)
        except Exception as exc:
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_SOURCE_UNAVAILABLE",
                    message="Skill source is unavailable in current runtime.",
                    details={
                        "source_id": source.id,
                        "source_type": source.type,
                        "locator": locator,
                        "reason": f"redis hgetall failed: {exc}",
                    },
                )
            )
            continue

        if not isinstance(meta, Mapping):
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "reason": "metadata row is not a mapping"},
                )
            )
            continue

        normalized: Dict[str, Any] = {}
        for mk, mv in meta.items():
            key_name = mk.decode("utf-8") if isinstance(mk, bytes) else str(mk)
            if isinstance(mv, bytes):
                normalized[key_name] = mv.decode("utf-8")
            else:
                normalized[key_name] = mv

        try:
            skill_name = ensure_metadata_string(
                normalized.get("skill_name"),
                field="skill_name",
                source_id=source.id,
                locator=locator,
            )
            if not is_valid_skill_name_slug(skill_name):
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "skill_name", "actual": skill_name},
                )
            description = ensure_metadata_string(
                normalized.get("description"),
                field="description",
                source_id=source.id,
                locator=locator,
            )
            created_at = ensure_metadata_string(
                normalized.get("created_at"),
                field="created_at",
                source_id=source.id,
                locator=locator,
            )
            body_size = normalize_optional_int(
                normalized.get("body_size"),
                field="body_size",
                source_id=source.id,
                locator=locator,
            )

            required_env_vars_parsed = parse_json_string_field(
                normalized.get("required_env_vars"),
                field="required_env_vars",
                source_id=source.id,
                locator=locator,
            )
            required_env_vars: List[str]
            if required_env_vars_parsed is None:
                required_env_vars = []
            elif isinstance(required_env_vars_parsed, list) and all(isinstance(v, str) for v in required_env_vars_parsed):
                required_env_vars = list(required_env_vars_parsed)
            else:
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "required_env_vars"},
                )

            metadata_parsed = parse_json_string_field(
                normalized.get("metadata"),
                field="metadata",
                source_id=source.id,
                locator=locator,
            )
            metadata_obj: Dict[str, Any]
            if metadata_parsed is None:
                metadata_obj = {}
            elif isinstance(metadata_parsed, dict):
                metadata_obj = dict(metadata_parsed)
            else:
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "metadata"},
                )

            body_key = normalized.get("body_key")
            if body_key is None:
                body_key = f"{key_prefix}body:{space.namespace}:{skill_name}"
            if not isinstance(body_key, str) or not body_key:
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "body_key"},
                )

            etag = normalized.get("etag")
            if etag is not None and not isinstance(etag, str):
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "etag"},
                )
            updated_at = normalized.get("updated_at")
            if updated_at is not None and not isinstance(updated_at, str):
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "updated_at"},
                )
            scope = normalized.get("scope")
            if scope is not None and not isinstance(scope, str):
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "scope"},
                )

            bundle_sha256 = normalized.get("bundle_sha256")
            if bundle_sha256 is not None:
                if not isinstance(bundle_sha256, str) or not is_sha256_hex(bundle_sha256):
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "bundle_sha256"},
                    )

            bundle_key = normalized.get("bundle_key")
            if bundle_key is not None and (not isinstance(bundle_key, str) or not bundle_key):
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "bundle_key"},
                )

            bundle_size = normalized.get("bundle_size")
            if bundle_size is not None:
                bundle_size = normalize_optional_int(bundle_size, field="bundle_size", source_id=source.id, locator=locator)

            bundle_format = normalized.get("bundle_format")
            if bundle_format is not None:
                if not isinstance(bundle_format, str) or str(bundle_format).strip().lower() != "zip":
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "bundle_format"},
                    )

            def _load_body(client_ref: Any = client, body_key_ref: str = body_key) -> str:
                """延迟加载 skill body（按 redis key 读取）。"""
                body_raw = client_ref.get(body_key_ref)
                if body_raw is None:
                    raise FileNotFoundError(f"missing body key: {body_key_ref}")
                if isinstance(body_raw, bytes):
                    return body_raw.decode("utf-8")
                if isinstance(body_raw, str):
                    return body_raw
                raise TypeError(f"invalid body type: {type(body_raw)!r}")

            sink.append(
                Skill(
                    space_id=space.id,
                    source_id=source.id,
                    namespace=space.namespace,
                    skill_name=skill_name,
                    description=description,
                    locator=locator,
                    path=None,
                    body_size=body_size,
                    body_loader=_load_body,
                    required_env_vars=required_env_vars,
                    metadata={
                        **metadata_obj,
                        "etag": etag,
                        "created_at": created_at,
                        "updated_at": updated_at,
                        "body_key": body_key,
                        "bundle_sha256": bundle_sha256,
                        "bundle_key": bundle_key,
                        "bundle_size": bundle_size,
                        "bundle_format": bundle_format,
                    },
                    scope=scope,
                )
            )
        except FrameworkError as exc:
            errors.append(exc.to_issue())
