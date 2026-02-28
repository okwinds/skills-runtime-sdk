"""
内置工具：skill_ref_read（Phase 3：Skills References）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/skills-actions.md`（ref-read 合约）

说明：
- 该工具只负责“受限引用读取”，默认仅允许 `references/`，可选允许 `assets/`。
- 当前版本仅支持 filesystem source（需要 bundle_root 边界来做路径校验）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from skills_runtime.core.errors import FrameworkError
from skills_runtime.skills.mentions import SkillMention, extract_skill_mentions
from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


SKILL_REF_READ_SPEC = ToolSpec(
    name="skill_ref_read",
    description="读取 skill bundle 内受限目录的引用文件（默认 references/，可选 assets/）。",
    parameters={
        "type": "object",
        "properties": {
            "skill_mention": {
                "type": "string",
                "description": "目标 skill 的全称 mention：$[namespace].skill_name",
            },
            "ref_path": {
                "type": "string",
                "description": "bundle 内引用路径（相对 bundle_root；必须位于 references/ 或（可选）assets/ 下）",
            },
            "max_bytes": {
                "type": "integer",
                "minimum": 1,
                "description": "最大读取字节数（可选；默认使用 skills.references.default_max_bytes）",
            },
        },
        "required": ["skill_mention", "ref_path"],
        "additionalProperties": False,
    },
    requires_approval=False,
    idempotency="safe",
)


def _framework_error_result(
    *,
    error_kind: str,
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    stderr: Optional[str] = None,
) -> ToolResult:
    """
    构造框架级结构化错误返回（ToolResultPayload + data.error）。

    参数：
    - error_kind：ToolResult 的错误分类（permission/validation/not_found/...）
    - code/message/details：英文结构化错误
    - stderr：可选；用于把一句话错误同时放入 stderr
    """

    err = {"code": code, "message": message, "details": dict(details or {})}
    return ToolResult.from_payload(
        ToolResultPayload(ok=False, stderr=stderr or message, exit_code=None, error_kind=error_kind, data={"error": err})
    )


def _parse_single_skill_mention(token: str) -> SkillMention:
    """
    解析并严格校验 skill mention：必须是“一个且仅一个”完整 token。

    参数：
    - token：形如 `$[namespace].skill_name` 的字符串

    返回：
    - SkillMention

    异常：
    - FrameworkError：当格式不合法时抛出（code=SKILL_MENTION_FORMAT_INVALID）
    """

    stripped = (token or "").strip()
    mentions = extract_skill_mentions(stripped)
    if len(mentions) != 1 or mentions[0].mention_text != stripped:
        raise FrameworkError(
            code="SKILL_MENTION_FORMAT_INVALID",
            message="Skill mention format is invalid. Use $[namespace].skill_name.",
            details={"mention": stripped, "reason": "not_a_single_full_token"},
        )
    return mentions[0]


def _is_references_enabled(ctx: ToolExecutionContext) -> bool:
    """
    判定 skills.references.enabled 是否开启（默认 fail-closed）。

    参数：
    - ctx：ToolExecutionContext（必须注入 skills_manager）
    """

    if ctx.skills_manager is None:
        return False
    cfg = getattr(ctx.skills_manager, "_skills_config", None)
    references = getattr(cfg, "references", None)
    enabled = getattr(references, "enabled", False)
    return bool(enabled)


def _references_allow_assets(ctx: ToolExecutionContext) -> bool:
    """返回 skills.references.allow_assets（默认 false）。"""

    if ctx.skills_manager is None:
        return False
    cfg = getattr(ctx.skills_manager, "_skills_config", None)
    references = getattr(cfg, "references", None)
    return bool(getattr(references, "allow_assets", False))


def _references_default_max_bytes(ctx: ToolExecutionContext) -> int:
    """返回 skills.references.default_max_bytes（缺省时回退到 ctx.max_file_bytes）。"""

    if ctx.skills_manager is None:
        return int(ctx.max_file_bytes)
    cfg = getattr(ctx.skills_manager, "_skills_config", None)
    references = getattr(cfg, "references", None)
    raw = getattr(references, "default_max_bytes", None)
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = int(ctx.max_file_bytes)
    return max(1, v)


def _validate_ref_path(*, ref_path: str, allow_assets: bool) -> tuple[str, Path]:
    """
    校验 ref_path 并返回允许的根目录名与 Path。

    参数：
    - ref_path：相对路径
    - allow_assets：是否允许 assets/

    返回：
    - (root_name, rel_path)

    异常：
    - FrameworkError(code=SKILL_REF_PATH_INVALID)
    """

    raw = (ref_path or "").strip()
    p = Path(raw)
    if not raw or p.is_absolute():
        raise FrameworkError(
            code="SKILL_REF_PATH_INVALID",
            message="ref_path must be a relative path within allowed directories.",
            details={"ref_path": raw},
        )
    if any(part in {"..", ""} for part in p.parts):
        raise FrameworkError(
            code="SKILL_REF_PATH_INVALID",
            message="ref_path must not contain '..' segments.",
            details={"ref_path": raw},
        )
    root = p.parts[0] if p.parts else ""
    if root == "references":
        return ("references", p)
    if root == "assets" and allow_assets:
        return ("assets", p)
    raise FrameworkError(
        code="SKILL_REF_PATH_INVALID",
        message="ref_path must start with 'references/' (or 'assets/' when enabled).",
        details={"ref_path": raw, "allow_assets": allow_assets},
    )


def _read_text_truncated(path: Path, *, max_bytes: int) -> tuple[str, bool]:
    """
    读取文本文件，并按 max_bytes 截断。

    参数：
    - path：已通过路径边界校验的绝对路径
    - max_bytes：最大读取字节数（>=1）

    返回：
    - (text, truncated)
    """

    max_bytes = max(1, int(max_bytes))
    with path.open("rb") as f:
        data = f.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text = f"{text}\n<truncated>"
    return text, truncated


def skill_ref_read(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    读取 skill bundle 的引用材料（filesystem + Redis bundle-backed）。

    参数：
    - call：ToolCall（args 必须包含 skill_mention/ref_path；可选 max_bytes）
    - ctx：ToolExecutionContext（必须注入 skills_manager）

    返回：
    - ToolResultPayload：stdout 为文件内容（必要时截断），truncated 标记截断
    """

    if not _is_references_enabled(ctx):
        return _framework_error_result(
            error_kind="permission",
            code="SKILL_REFERENCES_DISABLED",
            message="Skill references reading is disabled by configuration.",
            details={"config_key": "skills.references.enabled"},
        )

    extra_keys = set((call.args or {}).keys()) - {"skill_mention", "ref_path", "max_bytes"}
    if extra_keys:
        return _framework_error_result(
            error_kind="validation",
            code="SKILL_REF_PATH_INVALID",
            message="skill_ref_read arguments contain unknown fields.",
            details={"unknown_fields": sorted(str(k) for k in extra_keys)},
        )

    skill_mention = (call.args or {}).get("skill_mention")
    ref_path = (call.args or {}).get("ref_path")
    if not isinstance(skill_mention, str) or not isinstance(ref_path, str):
        return _framework_error_result(
            error_kind="validation",
            code="SKILL_REF_PATH_INVALID",
            message="skill_ref_read arguments are invalid.",
            details={"required": ["skill_mention", "ref_path"]},
        )

    try:
        mention = _parse_single_skill_mention(skill_mention)
    except FrameworkError as e:
        return _framework_error_result(error_kind="validation", code=e.code, message=e.message, details=e.details)

    if ctx.skills_manager is None:
        return _framework_error_result(
            error_kind="validation",
            code="SKILL_RUNTIME_UNAVAILABLE",
            message="SkillsManager is not available in tool execution context.",
            details={},
        )

    try:
        resolved = ctx.skills_manager.resolve_mentions(mention.mention_text)  # type: ignore[union-attr]
    except FrameworkError as e:
        kind = "validation"
        if e.code == "SKILL_UNKNOWN":
            kind = "not_found"
        return _framework_error_result(error_kind=kind, code=e.code, message=e.message, details=e.details)

    if not resolved:
        return _framework_error_result(
            error_kind="not_found",
            code="SKILL_UNKNOWN",
            message="Referenced skill is not found in configured spaces.",
            details={"mention": mention.mention_text},
        )
    skill, _ = resolved[0]
    try:
        bundle_root, bundle_sha256 = ctx.skills_manager.get_bundle_root_for_tool(skill=skill, purpose="references")  # type: ignore[union-attr]
    except FrameworkError as e:
        # 兼容：非支持 source 的语义仍对外暴露为 SKILL_REF_SOURCE_UNSUPPORTED
        if e.code == "SKILL_BUNDLE_SOURCE_UNSUPPORTED":
            e = FrameworkError(
                code="SKILL_REF_SOURCE_UNSUPPORTED",
                message="Skill references are only supported for filesystem and redis bundle-backed sources in current version.",
                details=dict(e.details or {}),
            )
        # 默认 validation；按更具体的错误码覆盖为 not_found/permission
        kind = "validation"
        if e.code == "SKILL_BUNDLE_NOT_FOUND":
            kind = "not_found"
        if e.code == "SKILL_BUNDLE_TOO_LARGE":
            kind = "permission"
        return _framework_error_result(error_kind=kind, code=e.code, message=e.message, details=e.details)

    # 本 change 的 Redis bundles 不支持 assets/（即使 config 开启也不放开）
    allow_assets = False if bundle_sha256 else _references_allow_assets(ctx)
    try:
        root_name, rel = _validate_ref_path(ref_path=ref_path, allow_assets=allow_assets)
    except FrameworkError as e:
        return _framework_error_result(error_kind="permission", code=e.code, message=e.message, details=e.details)

    allowed_dir = (bundle_root / root_name).resolve()
    candidate = (bundle_root / rel).resolve()
    if not candidate.is_relative_to(allowed_dir):
        return _framework_error_result(
            error_kind="permission",
            code="SKILL_REF_PATH_ESCAPE",
            message="ref_path escapes allowed directory boundary.",
            details={"ref_path": ref_path, "resolved": str(candidate), "allowed_dir": str(allowed_dir)},
        )
    if not candidate.exists() or not candidate.is_file():
        return _framework_error_result(
            error_kind="not_found",
            code="SKILL_REF_NOT_FOUND",
            message="Referenced file is not found in skill bundle.",
            details={"ref_path": ref_path, "resolved": str(candidate)},
        )

    max_bytes_raw = (call.args or {}).get("max_bytes")
    max_bytes = _references_default_max_bytes(ctx)
    if max_bytes_raw is not None:
        try:
            max_bytes = int(max_bytes_raw)
        except (TypeError, ValueError):
            return _framework_error_result(
                error_kind="validation",
                code="SKILL_REF_PATH_INVALID",
                message="max_bytes must be an integer.",
                details={"max_bytes": max_bytes_raw},
            )
        if max_bytes < 1:
            return _framework_error_result(
                error_kind="validation",
                code="SKILL_REF_PATH_INVALID",
                message="max_bytes must be >= 1.",
                details={"max_bytes": max_bytes},
            )

    text, truncated = _read_text_truncated(candidate, max_bytes=max_bytes)
    return ToolResult.from_payload(
        ToolResultPayload(ok=True, stdout=text, stderr="", exit_code=0, duration_ms=0, truncated=truncated)
    )
