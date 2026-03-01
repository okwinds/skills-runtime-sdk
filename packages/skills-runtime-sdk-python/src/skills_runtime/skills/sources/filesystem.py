from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from skills_runtime.config.loader import AgentSdkSkillsConfig
from skills_runtime.core.errors import FrameworkIssue
from skills_runtime.skills.loader import SkillLoadError, load_skill_metadata_from_path
from skills_runtime.skills.mentions import is_valid_skill_name_slug
from skills_runtime.skills.models import Skill
from skills_runtime.skills.sources._utils import utc_from_timestamp_rfc3339


def scan_filesystem_source(
    *,
    workspace_root: Path,
    scan_options: Dict[str, int | bool],
    space: AgentSdkSkillsConfig.Space,
    source: AgentSdkSkillsConfig.Source,
    sink: List[Skill],
    errors: List[FrameworkIssue],
) -> None:
    """Scan filesystem source (metadata-only; does not read body during scan)."""

    root = source.options.get("root")
    if not isinstance(root, str) or not root.strip():
        errors.append(
            FrameworkIssue(
                code="SKILL_SCAN_METADATA_INVALID",
                message="Filesystem source root is required.",
                details={"source_id": source.id},
            )
        )
        return

    fs_root = Path(root)
    if not fs_root.is_absolute():
        fs_root = (Path(workspace_root).resolve() / fs_root).resolve()
    if not fs_root.exists() or not fs_root.is_dir():
        return

    ignore_dot_entries = bool(scan_options["ignore_dot_entries"])
    max_depth = int(scan_options["max_depth"])
    max_dirs_per_root = int(scan_options["max_dirs_per_root"])

    visited_dirs = 0
    queue: List[tuple[Path, int]] = [(fs_root, 0)]
    while queue:
        cur, depth = queue.pop(0)
        visited_dirs += 1
        if max_dirs_per_root >= 1 and visited_dirs > max_dirs_per_root:
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill scan exceeded max directories per root.",
                    details={"source_id": source.id, "root": str(fs_root), "max_dirs_per_root": max_dirs_per_root},
                )
            )
            break
        if depth > max_depth:
            continue

        entries = sorted(cur.iterdir(), key=lambda p: p.name)
        for entry in entries:
            if ignore_dot_entries and entry.name.startswith("."):
                continue
            if entry.is_dir():
                queue.append((entry, depth + 1))
                continue
            if not entry.is_file() or entry.name != "SKILL.md":
                continue

            skill_md = entry
            try:
                loaded = load_skill_metadata_from_path(
                    skill_md,
                    max_frontmatter_bytes=int(scan_options["max_frontmatter_bytes"]),
                )
            except SkillLoadError as exc:
                errors.append(
                    FrameworkIssue(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={
                            "source_id": source.id,
                            "path": str(skill_md),
                            "reason": exc.message,
                        },
                    )
                )
                continue

            stat = skill_md.stat()
            if not is_valid_skill_name_slug(loaded.skill_name):
                errors.append(
                    FrameworkIssue(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={
                            "source_id": source.id,
                            "path": str(skill_md),
                            "field": "skill_name",
                            "actual": loaded.skill_name,
                            "reason": "invalid_skill_name_slug",
                        },
                    )
                )
                continue
            sink.append(
                Skill(
                    space_id=space.id,
                    source_id=source.id,
                    namespace=space.namespace,
                    skill_name=loaded.skill_name,
                    description=loaded.description,
                    locator=str(skill_md),
                    path=skill_md.resolve(),
                    body_size=int(stat.st_size),
                    body_loader=lambda p=skill_md.resolve(): p.read_text(encoding="utf-8"),
                    required_env_vars=list(loaded.required_env_vars),
                    metadata={**dict(loaded.metadata), "updated_at": utc_from_timestamp_rfc3339(stat.st_mtime)},
                    scope=loaded.scope,
                )
            )

