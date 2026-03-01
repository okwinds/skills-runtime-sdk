from __future__ import annotations

from pathlib import Path
from typing import Any, List, Tuple

from skills_runtime.core.errors import FrameworkError, FrameworkIssue
from skills_runtime.skills.mentions import SkillMention, extract_skill_mentions
from skills_runtime.skills.models import ScanReport, Skill


def perform_full_scan(
    manager,
) -> tuple[
    ScanReport,
    dict[tuple[str, str], Skill],
    dict[Path, Skill],
    dict[str, List[Skill]],
    FrameworkError | None,
]:
    """
    Perform a full scan once (ignores refresh_policy cache semantics).

    Returns:
    - report: ScanReport (metadata-only)
    - skills_by_key/path/name: indexes for this scan
    - fatal_exc: only for early-fail errors like duplicate (FrameworkError); else None
    """

    errors = manager._validate_config()
    warnings: List[Any] = []

    if errors:
        report = manager._make_scan_report(skills=[], errors=errors, warnings=warnings)
        return report, {}, {}, {}, None

    sources_map = manager._build_sources_map()
    scanned: List[Skill] = []

    for space in manager._skills_config.spaces:
        if not space.enabled:
            continue
        for source_id in space.sources:
            source = sources_map[source_id]
            if source.type == "filesystem":
                manager._scan_filesystem_source(space=space, source=source, sink=scanned, errors=errors)
            elif source.type == "in-memory":
                manager._scan_in_memory_source(space=space, source=source, sink=scanned, errors=errors)
            elif source.type == "redis":
                manager._scan_redis_source(space=space, source=source, sink=scanned, errors=errors)
            elif source.type == "pgsql":
                manager._scan_pgsql_source(space=space, source=source, sink=scanned, errors=errors)
            else:
                errors.append(
                    FrameworkIssue(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill source type is invalid.",
                        details={"source_id": source.id, "source_type": source.type},
                    )
                )

    scanned = sorted(scanned, key=lambda s: (s.skill_name, s.space_id, s.source_id, s.locator))
    try:
        manager._check_duplicates_or_raise(scanned)
    except FrameworkError as exc:
        report = manager._make_scan_report(skills=[], errors=[exc.to_issue(), *errors], warnings=warnings)
        return report, {}, {}, {}, exc

    skills_by_key = {(s.namespace, s.skill_name): s for s in scanned}
    skills_by_path = {s.path: s for s in scanned if s.path is not None}
    by_name: dict[str, List[Skill]] = {}
    for s in scanned:
        by_name.setdefault(s.skill_name, []).append(s)

    report = manager._make_scan_report(skills=scanned, errors=errors, warnings=warnings)
    return report, skills_by_key, skills_by_path, by_name, None


def scan(manager, *, force_refresh: bool = False) -> ScanReport:
    """Run skills scan and return a ScanReport (supports refresh_policy cache semantics)."""

    refresh_policy, ttl_sec = manager._scan_refresh_policy_from_config()
    cache_key = manager._scan_cache_key_for_current_config()

    with manager._scan_lock:
        cached_ok = None
        cached_ok_at = None
        if manager._scan_last_ok_report is not None and manager._scan_cache_key == cache_key:
            cached_ok = manager._scan_last_ok_report
            cached_ok_at = manager._scan_last_ok_at_monotonic

        if not force_refresh and refresh_policy == "ttl" and cached_ok is not None and cached_ok_at is not None:
            if (manager._now_monotonic() - float(cached_ok_at)) < float(ttl_sec):
                manager._scan_report = cached_ok
                return cached_ok

        if not force_refresh and refresh_policy == "manual" and cached_ok is not None:
            manager._scan_report = cached_ok
            return cached_ok

        report, skills_by_key, skills_by_path, skills_by_name, fatal_exc = perform_full_scan(manager)

        if fatal_exc is not None:
            if refresh_policy in {"ttl", "manual"} and cached_ok is not None:
                warn = manager._scan_refresh_failed_warning(refresh_policy=refresh_policy, reason=str(fatal_exc))
                fallback = manager._make_scan_report(skills=list(cached_ok.skills), errors=[], warnings=[warn])
                manager._scan_report = fallback
                return fallback

            manager._scan_report = report
            manager._skills_by_key = {}
            manager._skills_by_path = {}
            manager._skills_by_name = {}
            raise fatal_exc

        if report.errors and refresh_policy in {"ttl", "manual"} and cached_ok is not None:
            warn = manager._scan_refresh_failed_warning(
                refresh_policy=refresh_policy,
                reason=f"scan_errors: {[e.code for e in report.errors]}",
            )
            fallback = manager._make_scan_report(skills=list(cached_ok.skills), errors=[], warnings=[warn])
            manager._scan_report = fallback
            return fallback

        manager._scan_report = report
        manager._skills_by_key = skills_by_key
        manager._skills_by_path = skills_by_path
        manager._skills_by_name = skills_by_name

        if not report.errors:
            manager._scan_cache_key = cache_key
            manager._scan_last_ok_at_monotonic = manager._now_monotonic()
            manager._scan_last_ok_report = report

        return report


def resolve_mentions(manager, text: str) -> List[Tuple[Skill, SkillMention]]:
    """Resolve mentions and map them to skills."""

    mentions = extract_skill_mentions(text)
    if not mentions:
        return []

    spaces = [s for s in manager._skills_config.spaces if s.enabled]
    sources = manager._skills_config.sources
    if not spaces or not sources:
        manager._raise_space_not_configured(mentions[0])

    manager.scan()

    if manager._scan_report is not None:
        for issue in manager._scan_report.errors:
            if issue.code in {"SKILL_SCAN_SOURCE_UNAVAILABLE", "SKILL_SCAN_METADATA_INVALID"}:
                raise FrameworkError(code=issue.code, message=issue.message, details=dict(issue.details))

    out: List[Tuple[Skill, SkillMention]] = []
    seen = set()
    configured_spaces = {s.namespace for s in spaces}
    for mention in mentions:
        space_key = mention.namespace
        if space_key not in configured_spaces:
            manager._raise_space_not_configured(mention)

        key = (mention.namespace, mention.skill_name)
        skill = manager._skills_by_key.get(key)
        if skill is None:
            raise FrameworkError(
                code="SKILL_UNKNOWN",
                message="Referenced skill is not found in configured spaces.",
                details={"mention": mention.mention_text},
            )
        if skill.path is not None and skill.path in manager._disabled_paths:
            continue
        uniq = (skill.namespace, skill.skill_name)
        if uniq in seen:
            continue
        seen.add(uniq)
        out.append((skill, mention))
    return out


def render_injected_skill(manager, skill: Skill, *, source: str, mention_text: str | None = None) -> str:
    """Render injected skill (lazy-load body + max_bytes validation)."""

    _ = source
    _ = mention_text
    try:
        raw = skill.body_loader()
    except Exception as exc:
        raise FrameworkError(
            code="SKILL_BODY_READ_FAILED",
            message="Skill body read failed.",
            details={"skill_name": skill.skill_name, "locator": skill.locator, "reason": str(exc)},
        ) from exc

    raw_bytes = raw.encode("utf-8")
    limit = manager._skills_config.injection.max_bytes
    if limit is not None and len(raw_bytes) > limit:
        raise FrameworkError(
            code="SKILL_BODY_TOO_LARGE",
            message="Skill body exceeds configured max bytes.",
            details={
                "skill_name": skill.skill_name,
                "locator": skill.locator,
                "limit_bytes": limit,
                "actual_bytes": len(raw_bytes),
            },
        )

    locator = skill.locator
    parts: List[str] = [
        "<skill>",
        f"<name>{skill.skill_name}</name>",
        f"<path>{locator}</path>",
        raw,
        "</skill>",
    ]
    return "\n".join(parts)
