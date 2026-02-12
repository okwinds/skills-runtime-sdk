"""
SkillsManager（V2：配置驱动 scan + strict mentions + lazy-load 注入）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/skills.md`
- `docs/specs/skills-runtime-sdk/docs/configuration.md`
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import uuid

from agent_sdk.config.loader import AgentSdkSkillsConfig
from agent_sdk.core.errors import FrameworkError, FrameworkIssue, UserError
from agent_sdk.skills.loader import SkillLoadError, load_skill_metadata_from_path, load_skill_from_path
from agent_sdk.skills.mentions import (
    SkillMention,
    extract_skill_mentions,
    is_valid_account_slug,
    is_valid_domain_slug,
    is_valid_skill_name_slug,
)
from agent_sdk.skills.models import ScanReport, Skill


def _utc_now_rfc3339() -> str:
    """生成 UTC RFC3339 时间戳。"""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_from_timestamp_rfc3339(ts: float) -> str:
    """把 UNIX timestamp（秒）转换为 UTC RFC3339 字符串（以 `Z` 结尾）。"""

    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _scan_options_from_config(skills_config: AgentSdkSkillsConfig) -> dict[str, int | bool]:
    """从 skills 配置读取扫描参数（兼容旧 `skills.scan.*` 扩展字段）。"""

    extra = getattr(skills_config, "model_extra", None) or {}
    scan = extra.get("scan") if isinstance(extra, dict) else None
    if not isinstance(scan, dict):
        scan = {}

    def _safe_int(raw: Any, *, default: int, min_value: int) -> int:
        """
        将 raw 安全解析为 int（fail-open），并应用最小值约束。

        说明：
        - 运行时解析必须避免在构造期抛出 ValueError（例如 int("deep")）。
        - 对于非法值：回退到 default；对于过小值：回退到 min_value。
        """

        if raw is None:
            return default
        # bool 是 int 的子类；避免 True/False 被意外当作数字配置。
        if isinstance(raw, bool):
            return default
        try:
            value = int(raw)
        except Exception:
            return default
        if value < min_value:
            return min_value
        return value

    return {
        "ignore_dot_entries": bool(scan.get("ignore_dot_entries", True)),
        "max_depth": _safe_int(scan.get("max_depth", 99), default=99, min_value=0),
        "max_dirs_per_root": _safe_int(scan.get("max_dirs_per_root", 100000), default=100000, min_value=0),
        "max_frontmatter_bytes": _safe_int(scan.get("max_frontmatter_bytes", 65536), default=65536, min_value=1),
    }


class SkillsManager:
    """Skills 管理器（V2 语义）。"""

    _PREFLIGHT_ENV_VAR_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
    _PREFLIGHT_SUPPORTED_SOURCE_TYPES = {"filesystem", "in-memory", "redis", "pgsql"}

    def __init__(
        self,
        *,
        workspace_root: Path,
        skills_config: Optional[AgentSdkSkillsConfig | Dict[str, Any]] = None,
        roots: Optional[List[Path]] = None,
        in_memory_registry: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        source_clients: Optional[Dict[str, Any]] = None,
    ) -> None:
        """创建 SkillsManager。

        参数：
        - `workspace_root`：工作区根目录
        - `skills_config`：V2 skills 配置
        - `roots`：兼容旧接口；会映射成 filesystem source
        - `in_memory_registry`：in-memory source 注入注册表
        - `source_clients`：按 source_id 注入 redis/pgsql 客户端（用于离线测试/嵌入）
        """

        self._workspace_root = Path(workspace_root).resolve()
        self._in_memory_registry = in_memory_registry or {}
        self._source_clients = dict(source_clients or {})
        self._runtime_source_clients: Dict[str, Any] = {}

        if skills_config is None:
            legacy_roots = [str(Path(p)) for p in (roots or [])]
            spaces: List[AgentSdkSkillsConfig.Space] = []
            sources: List[AgentSdkSkillsConfig.Source] = []
            if legacy_roots:
                source_ids: List[str] = []
                for i, root in enumerate(legacy_roots):
                    sid = f"legacy-fs-{i}"
                    source_ids.append(sid)
                    sources.append(
                        AgentSdkSkillsConfig.Source.model_validate(
                            {"id": sid, "type": "filesystem", "options": {"root": root}}
                        )
                    )
                spaces.append(
                    AgentSdkSkillsConfig.Space.model_validate(
                        {
                            "id": "legacy-space",
                            "account": "legacy",
                            "domain": "local",
                            "sources": source_ids,
                            "enabled": True,
                        }
                    )
                )
            self._skills_config = AgentSdkSkillsConfig.model_validate(
                {
                    "roots": legacy_roots,
                    "mode": "explicit",
                    "max_auto": 3,
                    "spaces": [s.model_dump() for s in spaces],
                    "sources": [s.model_dump() for s in sources],
                    "injection": {"max_bytes": None},
                }
            )
        else:
            if isinstance(skills_config, dict):
                self._skills_config = AgentSdkSkillsConfig.model_validate(skills_config)
            else:
                self._skills_config = skills_config

        self._skills_by_key: Dict[Tuple[str, str, str], Skill] = {}
        self._skills_by_path: Dict[Path, Skill] = {}
        self._skills_by_name: Dict[str, List[Skill]] = {}
        self._scan_report: Optional[ScanReport] = None
        self._scan_lock = threading.RLock()
        self._scan_cache_key: Optional[str] = None
        self._scan_last_ok_at_monotonic: Optional[float] = None
        self._scan_last_ok_report: Optional[ScanReport] = None
        self._disabled_paths: set[Path] = set()
        self._scan_options = _scan_options_from_config(self._skills_config)

    @staticmethod
    def _now_monotonic() -> float:
        """返回单调时间（用于 TTL 计算；便于测试注入）。"""

        return time.monotonic()

    @property
    def workspace_root(self) -> Path:
        """返回 workspace 根目录。"""

        return self._workspace_root

    @property
    def scan_warnings(self) -> List[str]:
        """兼容旧接口：返回 warning messages。"""

        if self._scan_report is None:
            return []
        return [w.message for w in self._scan_report.warnings]

    @property
    def last_scan_report(self) -> Optional[ScanReport]:
        """
        返回最近一次 scan 过程中生成的 ScanReport（只读快照）。

        说明：
        - 当 `scan()` 因 `FrameworkError`（例如 duplicate）早失败抛错时，manager 仍会尽力在抛错前写入 `_scan_report`。
        - CLI 等上层可用该属性在失败时输出可观测报告，但不得修改 scan 的既有语义。
        """

        return self._scan_report

    def _scan_refresh_policy_from_config(self) -> tuple[str, int]:
        """
        从 skills.scan 读取 refresh_policy/ttl_sec（fail-open，保证默认不改变既有行为）。

        返回：
        - refresh_policy：always|ttl|manual（默认 always）
        - ttl_sec：int（默认 300；仅 ttl 生效；最小 1）
        """

        extra = getattr(self._skills_config, "model_extra", None) or {}
        scan = extra.get("scan") if isinstance(extra, dict) else None
        if not isinstance(scan, dict):
            scan = {}

        refresh_policy = scan.get("refresh_policy")
        if not isinstance(refresh_policy, str) or refresh_policy not in {"always", "ttl", "manual"}:
            refresh_policy = "always"

        ttl_raw = scan.get("ttl_sec", 300)
        try:
            ttl_sec = int(ttl_raw)
        except Exception:
            ttl_sec = 300
        if ttl_sec < 1:
            ttl_sec = 1

        return str(refresh_policy), int(ttl_sec)

    def _scan_cache_key_for_current_config(self) -> str:
        """为 scan 缓存生成 key（绑定 skills 配置 + scan options）。"""

        payload = {
            "skills": self._skills_config.model_dump(mode="json"),
            "scan_options": dict(self._scan_options),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def _scan_refresh_failed_warning(self, *, refresh_policy: str, reason: str) -> FrameworkIssue:
        """构造 refresh 失败但回退缓存时的 warning。"""

        return FrameworkIssue(
            code="SKILL_SCAN_REFRESH_FAILED",
            message="Skill scan refresh failed; returning cached result.",
            details={"refresh_policy": refresh_policy, "reason": reason},
        )

    def _perform_full_scan(self) -> tuple[ScanReport, Dict[Tuple[str, str, str], Skill], Dict[Path, Skill], Dict[str, List[Skill]], FrameworkError | None]:
        """
        执行一次完整 scan（不考虑 refresh_policy 缓存语义），并返回本次扫描快照。

        返回：
        - report：ScanReport（metadata-only）
        - skills_by_key/path/name：本次扫描构建的索引
        - fatal_exc：仅用于 duplicate 等“必须早失败”的异常（为 FrameworkError；否则为 None）
        """

        errors = self._validate_config()
        warnings: List[FrameworkIssue] = []

        if errors:
            report = self._make_scan_report(skills=[], errors=errors, warnings=warnings)
            return report, {}, {}, {}, None

        sources_map = self._build_sources_map()
        scanned: List[Skill] = []

        for space in self._skills_config.spaces:
            if not space.enabled:
                continue
            for source_id in space.sources:
                source = sources_map[source_id]
                if source.type == "filesystem":
                    self._scan_filesystem_source(space=space, source=source, sink=scanned, errors=errors)
                elif source.type == "in-memory":
                    self._scan_in_memory_source(space=space, source=source, sink=scanned, errors=errors)
                elif source.type == "redis":
                    self._scan_redis_source(space=space, source=source, sink=scanned, errors=errors)
                elif source.type == "pgsql":
                    self._scan_pgsql_source(space=space, source=source, sink=scanned, errors=errors)
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
            self._check_duplicates_or_raise(scanned)
        except FrameworkError as exc:
            report = self._make_scan_report(skills=[], errors=[exc.to_issue(), *errors], warnings=warnings)
            return report, {}, {}, {}, exc

        skills_by_key = {(s.account, s.domain, s.skill_name): s for s in scanned}
        skills_by_path = {s.path: s for s in scanned if s.path is not None}
        by_name: Dict[str, List[Skill]] = {}
        for s in scanned:
            by_name.setdefault(s.skill_name, []).append(s)

        report = self._make_scan_report(skills=scanned, errors=errors, warnings=warnings)
        return report, skills_by_key, skills_by_path, by_name, None

    def _build_sources_map(self) -> Dict[str, AgentSdkSkillsConfig.Source]:
        """构建 source id -> source 的映射。"""

        out: Dict[str, AgentSdkSkillsConfig.Source] = {}
        for source in self._skills_config.sources:
            src = source
            if isinstance(source, dict):
                src = AgentSdkSkillsConfig.Source.model_validate(source)
            out[src.id] = src
        return out

    def _validate_config(self) -> List[FrameworkIssue]:
        """验证 V2 skills 配置完整性。"""

        errors: List[FrameworkIssue] = []

        # Legacy fields：框架级不支持隐式 roots/auto discovery。
        if self._skills_config.roots:
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Legacy skills.roots is not supported. Use skills.spaces + skills.sources.",
                    details={"field": "skills.roots", "roots_total": len(self._skills_config.roots)},
                )
            )
        if self._skills_config.mode != "explicit":
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Legacy skills.mode is not supported. Use explicit spaces/sources only.",
                    details={"field": "skills.mode", "actual": self._skills_config.mode, "expected": "explicit"},
                )
            )

        sources_map = self._build_sources_map()
        spaces: List[AgentSdkSkillsConfig.Space] = []
        for space in self._skills_config.spaces:
            if isinstance(space, dict):
                spaces.append(AgentSdkSkillsConfig.Space.model_validate(space))
            else:
                spaces.append(space)
        self._skills_config = self._skills_config.model_copy(update={"spaces": spaces, "sources": list(sources_map.values())})

        valid_types = {"filesystem", "in-memory", "redis", "pgsql"}
        for source in self._skills_config.sources:
            if source.type not in valid_types:
                errors.append(
                    FrameworkIssue(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill source type is invalid.",
                        details={"source_id": source.id, "source_type": source.type},
                    )
                )

        for space in self._skills_config.spaces:
            if not is_valid_account_slug(space.account):
                errors.append(
                    FrameworkIssue(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={
                            "field": "skills.spaces[].account",
                            "space_id": space.id,
                            "actual": space.account,
                            "reason": "invalid_account_slug",
                        },
                    )
                )
            if not is_valid_domain_slug(space.domain):
                errors.append(
                    FrameworkIssue(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={
                            "field": "skills.spaces[].domain",
                            "space_id": space.id,
                            "actual": space.domain,
                            "reason": "invalid_domain_slug",
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

        return errors

    def preflight(self) -> List[FrameworkIssue]:
        """
        对 Skills 配置做零 I/O 的静态预检，返回 `FrameworkIssue` 列表（errors + warnings）。

        约束：
        - 不访问文件系统/redis/pgsql
        - 不读取环境变量内容（不访问 `os.environ`）

        返回：
        - issues：每条问题为英文结构化 `code/message/details`，其中 `details.path` 为点路径/索引路径
        """

        def _issue(*, code: str, message: str, path: str, details: Dict[str, Any] | None = None) -> FrameworkIssue:
            """
            构造一条 `FrameworkIssue`，并确保 `details.path` 存在。

            参数：
            - code：英文结构化错误码（例如 `SKILL_CONFIG_*`）。
            - message：英文可读说明（供日志/提示）。
            - path：点路径/索引路径（用于定位配置字段）。
            - details：额外结构化字段；会与 `{"path": path}` 合并。

            返回：
            - `FrameworkIssue` 实例（`details` 一定包含 `path` 字段）。

            异常：
            - TypeError：当 `details` 不是 dict（映射）类型时，`payload.update(details)` 可能触发。
            """
            payload: Dict[str, Any] = {"path": path}
            if details:
                payload.update(details)
            return FrameworkIssue(code=code, message=message, details=payload)

        issues: List[FrameworkIssue] = []

        # Legacy fields：避免“隐式发现/默认注入 roots”的误解（框架级不支持）。
        if self._skills_config.roots:
            issues.append(
                _issue(
                    code="SKILL_CONFIG_LEGACY_ROOTS_UNSUPPORTED",
                    message="Legacy skills.roots is not supported. Use skills.spaces + skills.sources.",
                    path="skills.roots",
                    details={"roots_total": len(self._skills_config.roots)},
                )
            )
        if self._skills_config.mode != "explicit":
            issues.append(
                _issue(
                    code="SKILL_CONFIG_LEGACY_MODE_UNSUPPORTED",
                    message="Legacy skills.mode is not supported. Use explicit spaces/sources only.",
                    path="skills.mode",
                    details={"actual": self._skills_config.mode, "expected": "explicit"},
                )
            )

        skills_extra = getattr(self._skills_config, "model_extra", None) or {}
        if isinstance(skills_extra, dict):
            for key in sorted(skills_extra.keys()):
                if key == "scan":
                    continue
                issues.append(
                    _issue(
                        code="SKILL_CONFIG_UNKNOWN_TOP_LEVEL_KEY",
                        message=f"Unknown skills config key: {key}",
                        path=f"skills.{key}",
                        details={"key": key, "allowed_extra_keys": ["scan"]},
                    )
                )

        scan_extra = skills_extra.get("scan") if isinstance(skills_extra, dict) else None
        if scan_extra is not None and not isinstance(scan_extra, dict):
            issues.append(
                _issue(
                    code="SKILL_CONFIG_INVALID_SCAN_OPTION",
                    message="Skills scan config must be an object.",
                    path="skills.scan",
                    details={"expected": "object", "actual": type(scan_extra).__name__},
                )
            )
        elif isinstance(scan_extra, dict):
            allowed_scan_keys = {
                "ignore_dot_entries",
                "max_depth",
                "max_dirs_per_root",
                "max_frontmatter_bytes",
                "refresh_policy",
                "ttl_sec",
            }
            for key in sorted(scan_extra.keys()):
                if key in allowed_scan_keys:
                    continue
                issues.append(
                    _issue(
                        code="SKILL_CONFIG_UNKNOWN_SCAN_OPTION",
                        message=f"Unknown skills.scan option: {key}",
                        path=f"skills.scan.{key}",
                        details={"key": key, "allowed_keys": sorted(allowed_scan_keys)},
                    )
                )

            def _invalid_scan_option(*, key: str, expected: str, actual_value: Any) -> None:
                """追加一条 scan option 校验错误（fail-closed）。"""

                issues.append(
                    _issue(
                        code="SKILL_CONFIG_INVALID_SCAN_OPTION",
                        message="Invalid skills.scan option.",
                        path=f"skills.scan.{key}",
                        details={"key": key, "expected": expected, "actual": type(actual_value).__name__},
                    )
                )

            ignore_dot_entries = scan_extra.get("ignore_dot_entries")
            if ignore_dot_entries is not None and not isinstance(ignore_dot_entries, bool):
                _invalid_scan_option(key="ignore_dot_entries", expected="bool", actual_value=ignore_dot_entries)

            max_depth = scan_extra.get("max_depth")
            if max_depth is not None and (not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 0):
                _invalid_scan_option(key="max_depth", expected="int >= 0", actual_value=max_depth)

            max_dirs_per_root = scan_extra.get("max_dirs_per_root")
            if max_dirs_per_root is not None and (
                not isinstance(max_dirs_per_root, int) or isinstance(max_dirs_per_root, bool) or max_dirs_per_root < 0
            ):
                _invalid_scan_option(key="max_dirs_per_root", expected="int >= 0", actual_value=max_dirs_per_root)

            max_frontmatter_bytes = scan_extra.get("max_frontmatter_bytes")
            if max_frontmatter_bytes is not None and (
                not isinstance(max_frontmatter_bytes, int)
                or isinstance(max_frontmatter_bytes, bool)
                or max_frontmatter_bytes < 1
            ):
                _invalid_scan_option(key="max_frontmatter_bytes", expected="int >= 1", actual_value=max_frontmatter_bytes)

            refresh_policy = scan_extra.get("refresh_policy")
            if refresh_policy is not None and (
                not isinstance(refresh_policy, str) or refresh_policy not in {"always", "ttl", "manual"}
            ):
                _invalid_scan_option(key="refresh_policy", expected="\"always\"|\"ttl\"|\"manual\"", actual_value=refresh_policy)

            ttl_sec = scan_extra.get("ttl_sec")
            if ttl_sec is not None and (not isinstance(ttl_sec, int) or isinstance(ttl_sec, bool) or ttl_sec < 1):
                _invalid_scan_option(key="ttl_sec", expected="int >= 1", actual_value=ttl_sec)

        def _unknown_nested_keys(
            *,
            obj: Any,
            base_path: str,
            code: str,
            message_prefix: str,
            level: str,
        ) -> None:
            """
            发现并记录对象 `model_extra` 中的未知配置键。

            用途：
            - Pydantic 模型可能携带 `model_extra`（未在 schema 中声明的键）；
            - 本函数将这些键转换为 `FrameworkIssue` 并追加到外层 `issues` 列表。

            参数：
            - obj：待检查对象（期望可通过 `getattr(obj, "model_extra", None)` 取到额外字段）。
            - base_path：`path` 的基准点路径（例如 `skills.versioning`）。
            - code：写入 `FrameworkIssue.code` 的错误码。
            - message_prefix：拼接到 message 的前缀（后续会追加具体键名）。
            - level：问题级别（写入 `details.level`，例如 `warning`）。

            返回：
            - 无。

            异常：
            - 无（不会主动抛出；仅在 `model_extra` 为 dict 时遍历）。
            """
            extra = getattr(obj, "model_extra", None) or {}
            if not isinstance(extra, dict):
                return
            for k in sorted(extra.keys()):
                issues.append(
                    _issue(
                        code=code,
                        message=f"{message_prefix}{k}",
                        path=f"{base_path}.{k}",
                        details={"key": k, "level": level},
                    )
                )

        _unknown_nested_keys(
            obj=self._skills_config.versioning,
            base_path="skills.versioning",
            code="SKILL_CONFIG_UNKNOWN_NESTED_KEY",
            message_prefix="Unknown skills.versioning config key: ",
            level="warning",
        )
        _unknown_nested_keys(
            obj=self._skills_config.strictness,
            base_path="skills.strictness",
            code="SKILL_CONFIG_UNKNOWN_NESTED_KEY",
            message_prefix="Unknown skills.strictness config key: ",
            level="warning",
        )

        spaces: List[AgentSdkSkillsConfig.Space] = []
        for idx, space in enumerate(self._skills_config.spaces):
            if isinstance(space, dict):
                spaces.append(AgentSdkSkillsConfig.Space.model_validate(space))
            else:
                spaces.append(space)
            _unknown_nested_keys(
                obj=spaces[-1],
                base_path=f"skills.spaces[{idx}]",
                code="SKILL_CONFIG_UNKNOWN_NESTED_KEY",
                message_prefix="Unknown skills.spaces[] config key: ",
                level="error",
            )

        sources: List[AgentSdkSkillsConfig.Source] = []
        for idx, source in enumerate(self._skills_config.sources):
            if isinstance(source, dict):
                sources.append(AgentSdkSkillsConfig.Source.model_validate(source))
            else:
                sources.append(source)
            _unknown_nested_keys(
                obj=sources[-1],
                base_path=f"skills.sources[{idx}]",
                code="SKILL_CONFIG_UNKNOWN_NESTED_KEY",
                message_prefix="Unknown skills.sources[] config key: ",
                level="error",
            )

        injection = self._skills_config.injection
        _unknown_nested_keys(
            obj=injection,
            base_path="skills.injection",
            code="SKILL_CONFIG_UNKNOWN_NESTED_KEY",
            message_prefix="Unknown skills.injection config key: ",
            level="error",
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
            if not is_valid_account_slug(space.account):
                issues.append(
                    _issue(
                        code="SKILL_CONFIG_INVALID_SPACE_SLUG",
                        message="Invalid skills space slug.",
                        path=f"skills.spaces[{idx}].account",
                        details={
                            "space_id": space.id,
                            "field": "account",
                            "actual": space.account,
                            "expected": "lowercase slug 2~32: [a-z0-9-], must start/end with [a-z0-9]",
                        },
                    )
                )
            if not is_valid_domain_slug(space.domain):
                issues.append(
                    _issue(
                        code="SKILL_CONFIG_INVALID_SPACE_SLUG",
                        message="Invalid skills space slug.",
                        path=f"skills.spaces[{idx}].domain",
                        details={
                            "space_id": space.id,
                            "field": "domain",
                            "actual": space.domain,
                            "expected": "lowercase slug 2~64: [a-z0-9-], must start/end with [a-z0-9]",
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
            if stype not in self._PREFLIGHT_SUPPORTED_SOURCE_TYPES:
                issues.append(
                    _issue(
                        code="SKILL_CONFIG_UNKNOWN_SOURCE_TYPE",
                        message="Unknown skills source type.",
                        path=f"skills.sources[{idx}].type",
                        details={
                            "source_id": source.id,
                            "actual": stype,
                            "supported": sorted(self._PREFLIGHT_SUPPORTED_SOURCE_TYPES),
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
                """
                断言指定的 source option 必须为非空字符串，否则追加一条 `FrameworkIssue`。

                参数：
                - option_key：`source.options` 中的字段名（例如 `dsn_env`、`root`）。

                返回：
                - 无（校验失败时以追加 issue 的方式报告，不抛出异常）。

                异常：
                - 无（不会主动抛出）。
                """
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
                if isinstance(dsn_env, str) and dsn_env.strip() and not self._PREFLIGHT_ENV_VAR_NAME_RE.match(dsn_env):
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

    def _make_scan_report(
        self,
        *,
        skills: List[Skill],
        errors: List[FrameworkIssue],
        warnings: List[FrameworkIssue],
    ) -> ScanReport:
        """构建 scan 报告对象。"""

        enabled_spaces = [s for s in self._skills_config.spaces if s.enabled]
        return ScanReport(
            scan_id=f"scan_{uuid.uuid4().hex[:12]}",
            skills=skills,
            errors=errors,
            warnings=warnings,
            stats={
                "spaces_total": len(enabled_spaces),
                "sources_total": len(self._skills_config.sources),
                "skills_total": len(skills),
            },
        )

    def _scan_filesystem_source(
        self,
        *,
        space: AgentSdkSkillsConfig.Space,
        source: AgentSdkSkillsConfig.Source,
        sink: List[Skill],
        errors: List[FrameworkIssue],
    ) -> None:
        """扫描 filesystem source（metadata-only，不读取正文）。"""

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
            fs_root = (self._workspace_root / fs_root).resolve()
        if not fs_root.exists() or not fs_root.is_dir():
            return

        ignore_dot_entries = bool(self._scan_options["ignore_dot_entries"])
        max_depth = int(self._scan_options["max_depth"])
        max_dirs_per_root = int(self._scan_options["max_dirs_per_root"])

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
                        max_frontmatter_bytes=int(self._scan_options["max_frontmatter_bytes"]),
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
                        account=space.account,
                        domain=space.domain,
                        skill_name=loaded.skill_name,
                        description=loaded.description,
                        locator=str(skill_md),
                        path=skill_md.resolve(),
                        body_size=int(stat.st_size),
                        body_loader=lambda p=skill_md.resolve(): p.read_text(encoding="utf-8"),
                        required_env_vars=list(loaded.required_env_vars),
                        metadata={**dict(loaded.metadata), "updated_at": _utc_from_timestamp_rfc3339(stat.st_mtime)},
                        scope=loaded.scope,
                    )
                )

    def _scan_in_memory_source(
        self,
        *,
        space: AgentSdkSkillsConfig.Space,
        source: AgentSdkSkillsConfig.Source,
        sink: List[Skill],
        errors: List[FrameworkIssue],
    ) -> None:
        """扫描 in-memory source。"""

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

        rows = self._in_memory_registry.get(namespace, [])
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
                    account=space.account,
                    domain=space.domain,
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

    def _source_dsn_from_env(self, source: AgentSdkSkillsConfig.Source) -> str:
        """从环境变量读取 source dsn。"""

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

    def _get_redis_client(self, source: AgentSdkSkillsConfig.Source) -> Any:
        """获取 redis client（优先注入，其次按 dsn_env 初始化）。"""

        injected = self._source_clients.get(source.id)
        if injected is not None:
            return injected
        cached = self._runtime_source_clients.get(source.id)
        if cached is not None:
            return cached

        dsn = self._source_dsn_from_env(source)
        try:
            import redis  # type: ignore[import-not-found]
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
        self._runtime_source_clients[source.id] = client
        return client

    def _get_pgsql_client(self, source: AgentSdkSkillsConfig.Source) -> Any:
        """获取 pgsql client（优先注入，其次按 dsn_env 初始化）。"""

        injected = self._source_clients.get(source.id)
        if injected is not None:
            return injected
        cached = self._runtime_source_clients.get(source.id)
        if cached is not None:
            return cached

        dsn = self._source_dsn_from_env(source)
        try:
            import psycopg  # type: ignore[import-not-found]
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
                    "reason": f"psycopg dependency unavailable: {exc}",
                },
            ) from exc

        try:
            client = psycopg.connect(dsn)
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
                    "reason": f"pgsql connect failed: {exc}",
                },
            ) from exc
        self._runtime_source_clients[source.id] = client
        return client

    def _parse_json_string_field(self, value: Any, *, field: str, source_id: str, locator: str) -> Any:
        """解析以 JSON 字符串编码的 metadata 字段。"""

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
        except Exception as exc:
            raise FrameworkError(
                code="SKILL_SCAN_METADATA_INVALID",
                message="Skill metadata is invalid.",
                details={"source_id": source_id, "locator": locator, "field": field, "reason": str(exc)},
            ) from exc

    def _ensure_metadata_string(self, value: Any, *, field: str, source_id: str, locator: str) -> str:
        """校验 metadata 字段为非空字符串。"""

        if not isinstance(value, str) or not value:
            raise FrameworkError(
                code="SKILL_SCAN_METADATA_INVALID",
                message="Skill metadata is invalid.",
                details={"source_id": source_id, "locator": locator, "field": field},
            )
        return value

    def _normalize_optional_int(self, value: Any, *, field: str, source_id: str, locator: str) -> Optional[int]:
        """把可选整数字段归一化为 int/None。"""

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

    def _scan_redis_source(
        self,
        *,
        space: AgentSdkSkillsConfig.Space,
        source: AgentSdkSkillsConfig.Source,
        sink: List[Skill],
        errors: List[FrameworkIssue],
    ) -> None:
        """扫描 redis source（metadata-only）。"""

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
            client = self._get_redis_client(source)
        except FrameworkError as exc:
            errors.append(exc.to_issue())
            return

        pattern = f"{key_prefix}meta:{space.account}:{space.domain}:*"
        try:
            keys_iter = client.scan_iter(match=pattern)
        except Exception as exc:
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_SOURCE_UNAVAILABLE",
                    message="Skill source is unavailable in current runtime.",
                    details={
                        "source_id": source.id,
                        "source_type": source.type,
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
                errors.append(
                    FrameworkIssue(
                        code="SKILL_SCAN_SOURCE_UNAVAILABLE",
                        message="Skill source is unavailable in current runtime.",
                        details={
                            "source_id": source.id,
                            "source_type": source.type,
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
                skill_name = self._ensure_metadata_string(
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
                description = self._ensure_metadata_string(
                    normalized.get("description"),
                    field="description",
                    source_id=source.id,
                    locator=locator,
                )
                created_at = self._ensure_metadata_string(
                    normalized.get("created_at"),
                    field="created_at",
                    source_id=source.id,
                    locator=locator,
                )
                body_size = self._normalize_optional_int(
                    normalized.get("body_size"),
                    field="body_size",
                    source_id=source.id,
                    locator=locator,
                )

                required_env_vars_parsed = self._parse_json_string_field(
                    normalized.get("required_env_vars"),
                    field="required_env_vars",
                    source_id=source.id,
                    locator=locator,
                )
                required_env_vars: List[str]
                if required_env_vars_parsed is None:
                    required_env_vars = []
                elif (
                    isinstance(required_env_vars_parsed, list)
                    and all(isinstance(v, str) for v in required_env_vars_parsed)
                ):
                    required_env_vars = list(required_env_vars_parsed)
                else:
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "required_env_vars"},
                    )

                metadata_parsed = self._parse_json_string_field(
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
                    body_key = f"{key_prefix}body:{space.account}:{space.domain}:{skill_name}"
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

                def _load_body(client_ref: Any = client, body_key_ref: str = body_key) -> str:
                    """
                    从 Redis 读取 skill body（作为 `Skill.body_loader` 的延迟加载回调）。

                    参数：
                    - client_ref：Redis 客户端引用（闭包默认捕获外层 `client`）。
                    - body_key_ref：Redis key（闭包默认捕获外层 `body_key`）。

                    返回：
                    - body 文本内容（`str`）；当底层存储为 `bytes` 时按 UTF-8 解码。

                    异常：
                    - FileNotFoundError：当 `body_key_ref` 不存在时。
                    - UnicodeDecodeError：当 `bytes` 无法按 UTF-8 解码时。
                    - TypeError：当读取到的 body 既不是 `bytes` 也不是 `str` 时。
                    """
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
                        account=space.account,
                        domain=space.domain,
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
                        },
                        scope=scope,
                    )
                )
            except FrameworkError as exc:
                errors.append(exc.to_issue())

    def _safe_identifier(self, raw: Any, *, field: str, source_id: str) -> str:
        """校验 SQL 标识符安全性。"""

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

    def _fetchall_as_rows(self, cursor: Any) -> List[Dict[str, Any]]:
        """把 DB 游标结果归一化为 dict rows。"""

        rows = cursor.fetchall()
        if not rows:
            return []

        if isinstance(rows[0], Mapping):
            return [dict(row) for row in rows]

        description = getattr(cursor, "description", None)
        if not description:
            raise TypeError("cursor.description is required for tuple rows")

        columns = [col[0] for col in description]
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                raise TypeError(f"unsupported row type: {type(row)!r}")
            out.append(dict(zip(columns, row, strict=False)))
        return out

    def _scan_pgsql_source(
        self,
        *,
        space: AgentSdkSkillsConfig.Space,
        source: AgentSdkSkillsConfig.Source,
        sink: List[Skill],
        errors: List[FrameworkIssue],
    ) -> None:
        """扫描 pgsql source（metadata-only）。"""

        try:
            schema = self._safe_identifier(source.options.get("schema"), field="schema", source_id=source.id)
            table = self._safe_identifier(source.options.get("table"), field="table", source_id=source.id)
        except FrameworkError as exc:
            errors.append(exc.to_issue())
            return

        try:
            client = self._get_pgsql_client(source)
        except FrameworkError as exc:
            errors.append(exc.to_issue())
            return

        table_ref = f'"{schema}"."{table}"'
        sql = (
            "SELECT id, account, domain, skill_name, description, body_size, body_etag, created_at, updated_at, "
            "required_env_vars, metadata, scope "
            f"FROM {table_ref} "
            "WHERE enabled = TRUE AND account = %s AND domain = %s"
        )

        try:
            with client.cursor() as cursor:
                cursor.execute(sql, (space.account, space.domain))
                rows = self._fetchall_as_rows(cursor)
        except Exception as exc:
            errors.append(
                FrameworkIssue(
                    code="SKILL_SCAN_SOURCE_UNAVAILABLE",
                    message="Skill source is unavailable in current runtime.",
                    details={
                        "source_id": source.id,
                        "source_type": source.type,
                        "reason": f"pgsql query failed: {exc}",
                    },
                )
            )
            return

        for row in rows:
            locator = f"{schema}.{table}#{row.get('id')}"
            try:
                skill_name = self._ensure_metadata_string(
                    row.get("skill_name"), field="skill_name", source_id=source.id, locator=locator
                )
                if not is_valid_skill_name_slug(skill_name):
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "skill_name", "actual": skill_name},
                    )
                description = self._ensure_metadata_string(
                    row.get("description"), field="description", source_id=source.id, locator=locator
                )
                body_size = self._normalize_optional_int(
                    row.get("body_size"), field="body_size", source_id=source.id, locator=locator
                )

                created_at_raw = row.get("created_at")
                if created_at_raw is None:
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "created_at"},
                    )
                if isinstance(created_at_raw, datetime):
                    created_at = created_at_raw.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                elif isinstance(created_at_raw, str) and created_at_raw:
                    created_at = created_at_raw
                else:
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "created_at"},
                    )

                required_env_vars_raw = row.get("required_env_vars")
                required_env_vars: List[str]
                if required_env_vars_raw is None:
                    required_env_vars = []
                elif isinstance(required_env_vars_raw, list) and all(isinstance(v, str) for v in required_env_vars_raw):
                    required_env_vars = list(required_env_vars_raw)
                else:
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "required_env_vars"},
                    )

                metadata_raw = row.get("metadata")
                metadata_obj: Dict[str, Any]
                if metadata_raw is None:
                    metadata_obj = {}
                elif isinstance(metadata_raw, dict):
                    metadata_obj = dict(metadata_raw)
                else:
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "metadata"},
                    )

                row_id = row.get("id")
                if row_id is None:
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "id"},
                    )

                scope = row.get("scope")
                if scope is not None and not isinstance(scope, str):
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "scope"},
                    )

                body_etag = row.get("body_etag")
                if body_etag is not None and not isinstance(body_etag, str):
                    raise FrameworkError(
                        code="SKILL_SCAN_METADATA_INVALID",
                        message="Skill metadata is invalid.",
                        details={"source_id": source.id, "locator": locator, "field": "body_etag"},
                    )

                updated_at = row.get("updated_at")
                if updated_at is not None and not isinstance(updated_at, str):
                    updated_at = str(updated_at)

                def _load_body(
                    client_ref: Any = client,
                    schema_ref: str = schema,
                    table_ref_inner: str = table,
                    row_id_ref: Any = row_id,
                    account_ref: str = space.account,
                    domain_ref: str = space.domain,
                ) -> str:
                    """
                    从 PostgreSQL 读取 skill body（作为 `Skill.body_loader` 的延迟加载回调）。

                    参数：
                    - client_ref：DB 连接（需提供 `cursor()` 上下文管理器）。
                    - schema_ref：schema 名称（已通过上层校验为安全标识符）。
                    - table_ref_inner：table 名称（已通过上层校验为安全标识符）。
                    - row_id_ref：记录主键/唯一标识（用于 `WHERE id = %s`）。
                    - account_ref：account 过滤条件。
                    - domain_ref：domain 过滤条件。

                    返回：
                    - body 文本内容（`str`）。

                    异常：
                    - FileNotFoundError：当找不到匹配记录时。
                    - TypeError：当读取到的 `body` 字段不是 `str` 时。
                    - Exception：数据库驱动在 `cursor/execute/fetchone` 过程中可能抛出连接/语法等异常。
                    """
                    sql_body = (
                        f'SELECT body FROM "{schema_ref}"."{table_ref_inner}" '
                        "WHERE id = %s AND account = %s AND domain = %s"
                    )
                    with client_ref.cursor() as body_cursor:
                        body_cursor.execute(sql_body, (row_id_ref, account_ref, domain_ref))
                        rec = body_cursor.fetchone()
                    if rec is None:
                        raise FileNotFoundError(f"missing body row: {schema_ref}.{table_ref_inner}#{row_id_ref}")
                    if isinstance(rec, Mapping):
                        body_val = rec.get("body")
                    elif isinstance(rec, (tuple, list)):
                        body_val = rec[0] if rec else None
                    else:
                        body_val = rec
                    if not isinstance(body_val, str):
                        raise TypeError(f"invalid body type: {type(body_val)!r}")
                    return body_val

                sink.append(
                    Skill(
                        space_id=space.id,
                        source_id=source.id,
                        account=space.account,
                        domain=space.domain,
                        skill_name=skill_name,
                        description=description,
                        locator=locator,
                        path=None,
                        body_size=body_size,
                        body_loader=_load_body,
                        required_env_vars=required_env_vars,
                        metadata={
                            **metadata_obj,
                            "etag": body_etag,
                            "created_at": created_at,
                            "updated_at": updated_at,
                            "row_id": row_id,
                        },
                        scope=scope,
                    )
                )
            except FrameworkError as exc:
                errors.append(exc.to_issue())

    def _scan_unavailable_source(
        self,
        *,
        source: AgentSdkSkillsConfig.Source,
        errors: List[FrameworkIssue],
    ) -> None:
        """记录当前阶段未实现 source 的可观测错误。"""

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

    def _check_duplicates_or_raise(self, skills: Sequence[Skill]) -> None:
        """全局 duplicate 检查（按 skill_name）。"""

        bucket: Dict[str, List[Skill]] = {}
        for skill in skills:
            bucket.setdefault(skill.skill_name, []).append(skill)

        for skill_name, members in bucket.items():
            if len(members) <= 1:
                continue
            conflicts = [
                {"space_id": it.space_id, "source_id": it.source_id, "locator": it.locator}
                for it in members
            ]
            raise FrameworkError(
                code="SKILL_DUPLICATE_NAME",
                message="Duplicate skill_name found across enabled spaces.",
                details={"skill_name": skill_name, "conflicts": conflicts},
            )

    def _check_legacy_name_conflicts(self, text: str) -> None:
        """识别 V2 外壳存在但 slug 含非法字符的场景。"""

        _ = text

    def scan(self, *, force_refresh: bool = False) -> ScanReport:
        """
        执行 V2 扫描并返回 `ScanReport`（支持 refresh_policy 缓存语义）。

        参数：
        - force_refresh：强制触发一次刷新 scan（manual/ttl 下可用于显式刷新缓存）
        """

        refresh_policy, ttl_sec = self._scan_refresh_policy_from_config()
        cache_key = self._scan_cache_key_for_current_config()

        with self._scan_lock:
            cached_ok = None
            cached_ok_at = None
            if self._scan_last_ok_report is not None and self._scan_cache_key == cache_key:
                cached_ok = self._scan_last_ok_report
                cached_ok_at = self._scan_last_ok_at_monotonic

            if not force_refresh and refresh_policy == "ttl" and cached_ok is not None and cached_ok_at is not None:
                if (self._now_monotonic() - float(cached_ok_at)) < float(ttl_sec):
                    self._scan_report = cached_ok
                    return cached_ok

            if not force_refresh and refresh_policy == "manual" and cached_ok is not None:
                self._scan_report = cached_ok
                return cached_ok

            report, skills_by_key, skills_by_path, skills_by_name, fatal_exc = self._perform_full_scan()

            # duplicate 等必须早失败：always / 无缓存时保持既有行为（raise）。
            if fatal_exc is not None:
                if refresh_policy in {"ttl", "manual"} and cached_ok is not None:
                    warn = self._scan_refresh_failed_warning(refresh_policy=refresh_policy, reason=str(fatal_exc))
                    fallback = self._make_scan_report(skills=list(cached_ok.skills), errors=[], warnings=[warn])
                    self._scan_report = fallback
                    return fallback

                self._scan_report = report
                self._skills_by_key = {}
                self._skills_by_path = {}
                self._skills_by_name = {}
                raise fatal_exc

            # refresh 失败但有历史成功缓存：返回旧缓存 + warnings（不得悄悄吞错）。
            if report.errors and refresh_policy in {"ttl", "manual"} and cached_ok is not None:
                warn = self._scan_refresh_failed_warning(
                    refresh_policy=refresh_policy,
                    reason=f"scan_errors: {[e.code for e in report.errors]}",
                )
                fallback = self._make_scan_report(skills=list(cached_ok.skills), errors=[], warnings=[warn])
                self._scan_report = fallback
                return fallback

            # commit 本次 scan 快照（无缓存 or always or refresh 成功）
            self._scan_report = report
            self._skills_by_key = skills_by_key
            self._skills_by_path = skills_by_path
            self._skills_by_name = skills_by_name

            if not report.errors:
                self._scan_cache_key = cache_key
                self._scan_last_ok_at_monotonic = self._now_monotonic()
                self._scan_last_ok_report = report

            return report

    def refresh(self) -> ScanReport:
        """显式触发一次 skills scan 刷新（等价于 `scan(force_refresh=True)`）。"""

        return self.scan(force_refresh=True)

    def list_skills(self, *, enabled_only: bool = False) -> List[Skill]:
        """返回已扫描 skills。"""

        if self._scan_report is None:
            return []
        items = list(self._scan_report.skills)
        if not enabled_only:
            return items
        return [s for s in items if not (s.path is not None and s.path in self._disabled_paths)]

    def set_enabled(self, skill_path: Path, enabled: bool) -> None:
        """兼容旧接口：V2 不支持 path 级开关。"""

        p = Path(skill_path).resolve()
        if p not in self._skills_by_path:
            raise UserError(f"未扫描到的 skill：{p}")
        if enabled:
            self._disabled_paths.discard(p)
        else:
            self._disabled_paths.add(p)

    def _raise_space_not_configured(self, mention: SkillMention) -> None:
        """抛出 space 未配置错误。"""

        raise FrameworkError(
            code="SKILL_SPACE_NOT_CONFIGURED",
            message="Skill space is not configured or disabled.",
            details={
                "mention": mention.mention_text,
                "account": mention.account,
                "domain": mention.domain,
            },
        )

    def resolve_mentions(self, text: str) -> List[Tuple[Skill, SkillMention]]:
        """解析 mentions 并映射到 skills。"""

        mentions = extract_skill_mentions(text)
        self._check_legacy_name_conflicts(text)
        if not mentions:
            return []

        spaces = [s for s in self._skills_config.spaces if s.enabled]
        sources = self._skills_config.sources
        if not spaces or not sources:
            self._raise_space_not_configured(mentions[0])

        # resolve 前按 refresh_policy 决定是否需要触发 scan/刷新（默认 always 保持“即时可见”）。
        self.scan()

        if self._scan_report is not None:
            for issue in self._scan_report.errors:
                if issue.code in {"SKILL_SCAN_SOURCE_UNAVAILABLE", "SKILL_SCAN_METADATA_INVALID"}:
                    raise FrameworkError(code=issue.code, message=issue.message, details=dict(issue.details))

        out: List[Tuple[Skill, SkillMention]] = []
        seen = set()
        configured_spaces = {(s.account, s.domain) for s in spaces}
        for mention in mentions:
            space_key = (mention.account, mention.domain)
            if space_key not in configured_spaces:
                self._raise_space_not_configured(mention)

            key = (mention.account, mention.domain, mention.skill_name)
            skill = self._skills_by_key.get(key)
            if skill is None:
                raise FrameworkError(
                    code="SKILL_UNKNOWN",
                    message="Referenced skill is not found in configured spaces.",
                    details={"mention": mention.mention_text},
                )
            if skill.path is not None and skill.path in self._disabled_paths:
                continue
            uniq = (skill.account, skill.domain, skill.skill_name)
            if uniq in seen:
                continue
            seen.add(uniq)
            out.append((skill, mention))
        return out

    def render_injected_skill(self, skill: Skill, *, source: str, mention_text: Optional[str] = None) -> str:
        """渲染注入技能（注入时懒加载正文 + max_bytes 校验）。"""

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
        limit = self._skills_config.injection.max_bytes
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
