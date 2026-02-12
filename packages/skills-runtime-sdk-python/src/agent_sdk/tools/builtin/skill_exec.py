"""
内置工具：skill_exec（Phase 3：Skills Actions）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/skills-actions.md`
- `docs/specs/skills-runtime-sdk/docs/tools.md`（approvals/sandbox 由 Agent gate 编排）

说明：
- skill_exec 不是“动态生成工具”，而是固定 builtin tool，通过 `action_id` 选择要执行的动作。
- 当前实现仅支持 filesystem source（因为需要稳定的 bundle_root 边界做路径校验）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from agent_sdk.core.errors import FrameworkError
from agent_sdk.skills.mentions import SkillMention, extract_skill_mentions
from agent_sdk.tools.builtin.shell_exec import shell_exec
from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext


SKILL_EXEC_SPEC = ToolSpec(
    name="skill_exec",
    description="执行一个已在 SKILL.md frontmatter.actions 中声明的 action（固定工具，不动态生成）。",
    parameters={
        "type": "object",
        "properties": {
            "skill_mention": {
                "type": "string",
                "description": "目标 skill 的全称 mention：$[account:domain].skill_name",
            },
            "action_id": {
                "type": "string",
                "description": "要执行的 action 标识（来自 SKILL.md frontmatter.actions.<action_id>）",
            },
        },
        "required": ["skill_mention", "action_id"],
        "additionalProperties": False,
    },
    requires_approval=True,
    idempotency="unknown",
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
    - code/message/details：英文结构化错误（稳定枚举 + 可读句子 + 结构化上下文）
    - stderr：可选；用于把一句话错误同时放入 stderr（便于 CLI/日志定位）
    """

    err = {"code": code, "message": message, "details": dict(details or {})}
    return ToolResult.from_payload(
        ToolResultPayload(ok=False, stderr=stderr or message, exit_code=None, error_kind=error_kind, data={"error": err})
    )


def _parse_single_skill_mention(token: str) -> SkillMention:
    """
    解析并严格校验 skill mention：必须是“一个且仅一个”完整 token。

    参数：
    - token：形如 `$[account:domain].skill_name` 的字符串

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
            message="Skill mention format is invalid. Use $[account:domain].skill_name.",
            details={"mention": stripped, "reason": "not_a_single_full_token"},
        )
    return mentions[0]


@dataclass(frozen=True)
class _FilesystemBundle:
    """filesystem skill bundle 的最小运行时投影。"""

    mention_text: str
    action_id: str
    bundle_root: Path
    action_def: Dict[str, Any]


def _resolve_filesystem_bundle(
    *,
    ctx: ToolExecutionContext,
    skill_mention: str,
    action_id: str,
) -> _FilesystemBundle:
    """
    从 mention/action_id 定位到 filesystem bundle_root，并读取 action 定义（metadata-only）。

    参数：
    - ctx：ToolExecutionContext（必须注入 skills_manager）
    - skill_mention：完整 mention token
    - action_id：frontmatter.actions 下的 key

    返回：
    - _FilesystemBundle（bundle_root + action_def）
    """

    if ctx.skills_manager is None:
        raise FrameworkError(
            code="SKILL_RUNTIME_UNAVAILABLE",
            message="SkillsManager is not available in tool execution context.",
            details={},
        )

    mention = _parse_single_skill_mention(skill_mention)
    resolved = ctx.skills_manager.resolve_mentions(mention.mention_text)  # type: ignore[union-attr]
    if not resolved:
        raise FrameworkError(
            code="SKILL_UNKNOWN",
            message="Referenced skill is not found in configured spaces.",
            details={"mention": mention.mention_text},
        )
    skill, _ = resolved[0]
    if skill.path is None:
        raise FrameworkError(
            code="SKILL_ACTION_SOURCE_UNSUPPORTED",
            message="Skill actions are only supported for filesystem sources in current version.",
            details={"source_id": skill.source_id, "locator": skill.locator},
        )
    skill_md = Path(skill.path)
    bundle_root = skill_md.parent

    actions = (skill.metadata or {}).get("actions")
    if not isinstance(actions, dict):
        actions = {}
    action_def = actions.get(action_id)
    if action_def is None:
        raise FrameworkError(
            code="SKILL_ACTION_NOT_FOUND",
            message="Skill action is not found in SKILL.md frontmatter.",
            details={"mention": mention.mention_text, "action_id": action_id},
        )
    if not isinstance(action_def, dict):
        raise FrameworkError(
            code="SKILL_ACTION_DEFINITION_INVALID",
            message="Skill action definition is invalid. Expected an object.",
            details={"mention": mention.mention_text, "action_id": action_id},
        )
    return _FilesystemBundle(
        mention_text=mention.mention_text,
        action_id=action_id,
        bundle_root=bundle_root,
        action_def=dict(action_def),
    )


def _is_actions_enabled(ctx: ToolExecutionContext) -> bool:
    """
    判定 skills.actions.enabled 是否开启（默认 fail-closed）。

    参数：
    - ctx：ToolExecutionContext（必须注入 skills_manager）
    """

    if ctx.skills_manager is None:
        return False
    cfg = getattr(ctx.skills_manager, "_skills_config", None)
    actions = getattr(cfg, "actions", None)
    enabled = getattr(actions, "enabled", False)
    return bool(enabled)


def _validate_and_materialize_shell_argv(*, bundle_root: Path, argv: Any) -> list[str]:
    """
    校验 action.argv，并将 bundle 内脚本路径 materialize 成绝对路径。

    规则（当前版本最小可回归集合）：
    - argv 必须是 string[]，且长度 >= 1
    - argv[1..] 中“看起来像路径”的参数（包含 `/` 或以 `.` 开头）必须位于 `actions/` 下
    - 禁止绝对路径、禁止 `..`，禁止 realpath 后不在 `<bundle_root>/actions/` 前缀内
    - 指向的脚本文件必须存在
    """

    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise FrameworkError(
            code="SKILL_ACTION_DEFINITION_INVALID",
            message="Skill action definition is invalid. Expected argv to be a list of strings.",
            details={"field": "argv", "expected": "string[]"},
        )

    out = list(argv)
    actions_dir = (bundle_root / "actions").resolve()

    for i in range(1, len(out)):
        raw = out[i]
        if not raw:
            continue
        looks_like_path = ("/" in raw) or raw.startswith(".")
        if not looks_like_path:
            continue
        if raw.startswith("/"):
            raise FrameworkError(
                code="SKILL_ACTION_ARGV_PATH_ESCAPE",
                message="Action argv path must be relative and stay within bundle actions/ directory.",
                details={"argv_index": i, "path": raw},
            )
        if not raw.startswith("actions/"):
            raise FrameworkError(
                code="SKILL_ACTION_ARGV_PATH_ESCAPE",
                message="Action argv path must stay within bundle actions/ directory.",
                details={"argv_index": i, "path": raw, "required_prefix": "actions/"},
            )
        p = Path(raw)
        if any(part == ".." for part in p.parts):
            raise FrameworkError(
                code="SKILL_ACTION_ARGV_PATH_ESCAPE",
                message="Action argv path must not contain '..'.",
                details={"argv_index": i, "path": raw},
            )
        resolved = (bundle_root / p).resolve()
        if not resolved.is_relative_to(actions_dir):
            raise FrameworkError(
                code="SKILL_ACTION_ARGV_PATH_ESCAPE",
                message="Action argv path escapes allowed actions/ directory.",
                details={"argv_index": i, "path": raw, "resolved": str(resolved)},
            )
        if not resolved.exists() or not resolved.is_file():
            raise FrameworkError(
                code="SKILL_ACTION_ARGV_PATH_INVALID",
                message="Action argv script path does not exist in bundle.",
                details={"argv_index": i, "path": raw, "resolved": str(resolved)},
            )
        out[i] = str(resolved)

    return out


def skill_exec(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行一个 skill action（Phase 3：filesystem-only）。

    参数：
    - call：ToolCall（args 必须包含 skill_mention/action_id）
    - ctx：ToolExecutionContext（需要注入 skills_manager + executor）

    返回：
    - ToolResult：与 `shell_exec` 对齐的 ToolResultPayload；框架错误使用 `data.error`（英文结构化）
    """

    if not _is_actions_enabled(ctx):
        return _framework_error_result(
            error_kind="permission",
            code="SKILL_ACTIONS_DISABLED",
            message="Skill actions are disabled by configuration.",
            details={"config_key": "skills.actions.enabled"},
        )

    extra_keys = set((call.args or {}).keys()) - {"skill_mention", "action_id"}
    if extra_keys:
        return _framework_error_result(
            error_kind="validation",
            code="SKILL_ACTION_DEFINITION_INVALID",
            message="skill_exec arguments contain unknown fields.",
            details={"unknown_fields": sorted(str(k) for k in extra_keys)},
        )

    skill_mention = (call.args or {}).get("skill_mention")
    action_id = (call.args or {}).get("action_id")
    if not isinstance(skill_mention, str) or not isinstance(action_id, str) or not skill_mention.strip() or not action_id.strip():
        return _framework_error_result(
            error_kind="validation",
            code="SKILL_ACTION_DEFINITION_INVALID",
            message="skill_exec arguments are invalid.",
            details={"required": ["skill_mention", "action_id"]},
        )

    try:
        bundle = _resolve_filesystem_bundle(ctx=ctx, skill_mention=skill_mention, action_id=action_id.strip())
    except FrameworkError as e:
        # mention/source/action 定位类错误：映射到 tool error_kind
        kind = "validation"
        if e.code in {"SKILL_UNKNOWN", "SKILL_ACTION_NOT_FOUND"}:
            kind = "not_found"
        if e.code in {"SKILL_MENTION_FORMAT_INVALID"}:
            kind = "validation"
        if e.code in {"SKILL_ACTIONS_DISABLED", "SKILL_ACTION_ARGV_PATH_ESCAPE"}:
            kind = "permission"
        if e.code in {"SKILL_ACTION_SOURCE_UNSUPPORTED"}:
            kind = "validation"
        return _framework_error_result(error_kind=kind, code=e.code, message=e.message, details=e.details)

    # action definition（metadata-only）
    argv_raw = bundle.action_def.get("argv")
    timeout_ms = bundle.action_def.get("timeout_ms")
    env_raw = bundle.action_def.get("env")
    kind_raw = bundle.action_def.get("kind", "shell")

    if kind_raw is not None and not isinstance(kind_raw, str):
        return _framework_error_result(
            error_kind="validation",
            code="SKILL_ACTION_DEFINITION_INVALID",
            message="Skill action definition is invalid. Expected kind to be a string.",
            details={"mention": bundle.mention_text, "action_id": bundle.action_id},
        )
    kind = str(kind_raw or "shell").strip().lower()
    if kind != "shell":
        return _framework_error_result(
            error_kind="validation",
            code="SKILL_ACTION_DEFINITION_INVALID",
            message="Only shell actions are supported in current version.",
            details={"mention": bundle.mention_text, "action_id": bundle.action_id, "kind": kind},
        )

    try:
        argv = _validate_and_materialize_shell_argv(bundle_root=bundle.bundle_root, argv=argv_raw)
    except FrameworkError as e:
        return _framework_error_result(error_kind="permission" if e.code.endswith("_ESCAPE") else "validation", code=e.code, message=e.message, details=e.details)

    env: Dict[str, str] = {}
    if env_raw is not None:
        if not isinstance(env_raw, Mapping) or not all(isinstance(k, str) and isinstance(v, (str, int, float, bool)) for k, v in env_raw.items()):
            return _framework_error_result(
                error_kind="validation",
                code="SKILL_ACTION_DEFINITION_INVALID",
                message="Skill action env is invalid. Expected a string map.",
                details={"mention": bundle.mention_text, "action_id": bundle.action_id},
            )
        env.update({str(k): str(v) for k, v in env_raw.items()})

    stable_env = {
        "SKILLS_RUNTIME_SDK_WORKSPACE_ROOT": str(ctx.workspace_root.resolve()),
        "SKILLS_RUNTIME_SDK_SKILL_BUNDLE_ROOT": str(bundle.bundle_root.resolve()),
        "SKILLS_RUNTIME_SDK_SKILL_MENTION": bundle.mention_text,
        "SKILLS_RUNTIME_SDK_SKILL_ACTION_ID": bundle.action_id,
        # 旧前缀兼容：双写，便于下游 actions/脚本渐进迁移。
        "AGENT_SDK_WORKSPACE_ROOT": str(ctx.workspace_root.resolve()),
        "AGENT_SDK_SKILL_BUNDLE_ROOT": str(bundle.bundle_root.resolve()),
        "AGENT_SDK_SKILL_MENTION": bundle.mention_text,
        "AGENT_SDK_SKILL_ACTION_ID": bundle.action_id,
    }
    env.update(stable_env)  # stable keys win

    timeout: Optional[int] = None
    if timeout_ms is not None:
        try:
            timeout = int(timeout_ms)
        except Exception:
            return _framework_error_result(
                error_kind="validation",
                code="SKILL_ACTION_DEFINITION_INVALID",
                message="Skill action timeout_ms is invalid. Expected integer milliseconds.",
                details={"mention": bundle.mention_text, "action_id": bundle.action_id, "timeout_ms": timeout_ms},
            )
        if timeout < 1:
            return _framework_error_result(
                error_kind="validation",
                code="SKILL_ACTION_DEFINITION_INVALID",
                message="Skill action timeout_ms must be >= 1.",
                details={"mention": bundle.mention_text, "action_id": bundle.action_id, "timeout_ms": timeout},
            )

    # 复用 shell_exec（保证 sandbox 语义与输出字段一致）；approval gate 在 Agent 层对 skill_exec 生效。
    inner = ToolCall(
        call_id=call.call_id,
        name="shell_exec",
        args={
            "argv": argv,
            "env": env,
            "timeout_ms": timeout,
            "sandbox": "inherit",
        },
    )
    return shell_exec(inner, ctx)
