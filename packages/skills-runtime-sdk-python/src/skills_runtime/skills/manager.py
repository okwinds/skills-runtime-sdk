"""
SkillsManager（配置驱动 scan + strict mentions + lazy-load 注入）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/skills.md`
- `docs/specs/skills-runtime-sdk/docs/configuration.md`
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
from types import TracebackType
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid

from skills_runtime.config.loader import AgentSdkSkillsConfig
from skills_runtime.core.errors import FrameworkError, FrameworkIssue, UserError
from skills_runtime.skills.bundles import ExtractedBundle
from skills_runtime.skills.models import ScanReport, Skill
from skills_runtime.skills.manager_ops import (
    render_injected_skill as _render_injected_skill,
    resolve_mentions as _resolve_mentions,
    scan as _scan,
)

from skills_runtime.skills.bundle_cache import (
    bundle_cache_root as _bundle_cache_root,
    get_bundle_root_for_tool as _get_bundle_root_for_tool,
)
from skills_runtime.skills.config_validator import (
    preflight as _preflight_config,
    scan_options_from_config as _scan_options_from_config,
    validate_and_normalize_config as _validate_and_normalize_config,
)
from skills_runtime.skills.sources._utils import source_dsn_from_env as _source_dsn_from_env
from skills_runtime.skills.sources.filesystem import scan_filesystem_source as _scan_filesystem_source_impl
from skills_runtime.skills.sources.in_memory import scan_in_memory_source as _scan_in_memory_source_impl
from skills_runtime.skills.sources.pgsql import (
    get_pgsql_client as _get_pgsql_client_impl,
    pgsql_client_context as _pgsql_client_context_impl,
    scan_pgsql_source as _scan_pgsql_source_impl,
)
from skills_runtime.skills.sources.redis import (
    ensure_redis_bundle_extracted as _ensure_redis_bundle_extracted_impl,
    get_redis_client as _get_redis_client_impl,
    scan_redis_source as _scan_redis_source_impl,
)
class SkillsManager:
    """Skills 管理器。"""
    def __init__(
        self,
        *,
        workspace_root: Path,
        skills_config: Optional[AgentSdkSkillsConfig | Dict[str, Any]] = None,
        in_memory_registry: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        source_clients: Optional[Dict[str, Any]] = None,
    ) -> None:
        """创建 SkillsManager。

        参数：
        - `workspace_root`：工作区根目录
        - `skills_config`：skills 配置
        - `in_memory_registry`：in-memory source 注入注册表
        - `source_clients`：按 source_id 注入 redis/pgsql 客户端（用于离线测试/嵌入）
        """
        self._workspace_root = Path(workspace_root).resolve()
        self._in_memory_registry = in_memory_registry or {}
        self._source_clients = dict(source_clients or {})
        self._runtime_source_clients: Dict[str, Any] = {}

        if skills_config is None:
            self._skills_config = AgentSdkSkillsConfig()
        elif isinstance(skills_config, dict):
            self._skills_config = AgentSdkSkillsConfig.model_validate(skills_config)
        else:
            self._skills_config = skills_config

        self._skills_by_key: Dict[Tuple[str, str], Skill] = {}
        self._skills_by_path: Dict[Path, Skill] = {}
        self._skills_by_name: Dict[str, List[Skill]] = {}
        self._scan_report: Optional[ScanReport] = None
        self._scan_lock = threading.RLock()
        self._scan_cache_key: Optional[str] = None
        self._scan_last_ok_at_monotonic: Optional[float] = None
        self._scan_last_ok_report: Optional[ScanReport] = None
        self._disabled_paths: set[Path] = set()
        self._scan_options = _scan_options_from_config(self._skills_config)
        bundles_cfg = getattr(self._skills_config, "bundles", None)
        self._bundle_max_bytes = int(getattr(bundles_cfg, "max_bytes", 1 * 1024 * 1024) or 1 * 1024 * 1024)
        self._bundle_cache_dir_raw = str(getattr(bundles_cfg, "cache_dir", ".skills_runtime_sdk/bundles") or ".skills_runtime_sdk/bundles")
        self._bundle_max_extracted_bytes = getattr(bundles_cfg, "max_extracted_bytes", None)
        self._bundle_max_files = getattr(bundles_cfg, "max_files", None)
        self._bundle_max_single_file_bytes = getattr(bundles_cfg, "max_single_file_bytes", None)

    def _bundle_cache_root(self) -> Path:
        """bundle 解压缓存根目录（runtime-owned，可删可重建）。"""
        return _bundle_cache_root(workspace_root=self._workspace_root, cache_dir_raw=self._bundle_cache_dir_raw)

    def _find_source_by_id(self, source_id: str) -> AgentSdkSkillsConfig.Source:
        """按 source_id 查找 source 配置，找不到则 fail-fast。"""
        for s in self._skills_config.sources:
            if s.id == source_id:
                return s
        raise FrameworkError(
            code="SKILL_SCAN_METADATA_INVALID",
            message="Skill source is not configured.",
            details={"source_id": source_id},
        )

    def _ensure_redis_bundle_extracted(self, *, skill: Skill) -> ExtractedBundle:
        """确保 redis bundle 已解压到本地缓存（供正文/工具执行使用）。"""
        return _ensure_redis_bundle_extracted_impl(
            skill=skill,
            find_source_by_id=self._find_source_by_id,
            get_redis_client_for_source=self._get_redis_client,
            bundle_cache_root=self._bundle_cache_root(),
            bundle_max_bytes=self._bundle_max_bytes,
            bundle_max_extracted_bytes=int(self._bundle_max_extracted_bytes) if self._bundle_max_extracted_bytes is not None else None,
            bundle_max_files=int(self._bundle_max_files) if self._bundle_max_files is not None else None,
            bundle_max_single_file_bytes=int(self._bundle_max_single_file_bytes) if self._bundle_max_single_file_bytes is not None else None,
        )

    def get_bundle_root_for_tool(self, *, skill: Skill, purpose: str) -> tuple[Path, Optional[str]]:
        """为某个 skill/tool purpose 返回 bundle root（必要时触发解压）。"""
        return _get_bundle_root_for_tool(
            skill=skill,
            purpose=purpose,
            ensure_redis_bundle_extracted=self._ensure_redis_bundle_extracted,
        )

    @staticmethod
    def _now_monotonic() -> float:
        """返回单调时间（用于 TTL 计算；便于测试注入）。"""
        return time.monotonic()

    @property
    def workspace_root(self) -> Path:
        """返回 workspace 根目录。"""
        return self._workspace_root

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
        从 skills.scan 读取 refresh_policy/ttl_sec。

        返回：
        - refresh_policy：always|ttl|manual
        - ttl_sec：int（仅 ttl 生效）
        """
        scan = self._skills_config.scan
        return str(scan.refresh_policy), int(scan.ttl_sec)

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
        """验证 skills 配置完整性。"""
        normalized, errors = _validate_and_normalize_config(self._skills_config)
        self._skills_config = normalized
        return errors

    def preflight(self) -> List[FrameworkIssue]:
        """对 skills 配置做零 I/O 预检（不触碰外部系统）。"""
        return _preflight_config(self._skills_config)

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
        _scan_filesystem_source_impl(
            workspace_root=self._workspace_root,
            scan_options=dict(self._scan_options),
            space=space,
            source=source,
            sink=sink,
            errors=errors,
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
        _scan_in_memory_source_impl(
            in_memory_registry=self._in_memory_registry,
            space=space,
            source=source,
            sink=sink,
            errors=errors,
        )

    def _source_dsn_from_env(self, source: AgentSdkSkillsConfig.Source) -> str:
        """从环境变量读取 source dsn。"""
        return _source_dsn_from_env(source)

    def _get_redis_client(self, source: AgentSdkSkillsConfig.Source) -> Any:
        """获取 redis client（优先注入，其次按 dsn_env 初始化）。"""
        return _get_redis_client_impl(
            source=source,
            source_clients=self._source_clients,
            runtime_source_clients=self._runtime_source_clients,
            source_dsn_from_env=self._source_dsn_from_env,
        )

    def _get_pgsql_client(self, source: AgentSdkSkillsConfig.Source) -> Any:
        """获取 pgsql client（优先注入，其次按 dsn_env 初始化）。"""
        return _get_pgsql_client_impl(
            source=source,
            source_clients=self._source_clients,
            source_dsn_from_env=self._source_dsn_from_env,
        )

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
        except json.JSONDecodeError as exc:
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
        _scan_redis_source_impl(
            space=space,
            source=source,
            sink=sink,
            errors=errors,
            get_redis_client_for_source=self._get_redis_client,
        )

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

        def _client_ctx(src: AgentSdkSkillsConfig.Source):
            """为 pgsql source 提供 client 上下文管理器（支持注入 client）。"""
            return _pgsql_client_context_impl(
                source=src,
                source_clients=self._source_clients,
                get_pgsql_client_for_source=lambda s: self._get_pgsql_client(s),
            )

        _scan_pgsql_source_impl(
            space=space,
            source=source,
            sink=sink,
            errors=errors,
            pgsql_client_context_for_source=_client_ctx,
        )

    def _check_duplicates_or_raise(self, skills: Sequence[Skill]) -> None:
        """全局 duplicate 检查（按 namespace + skill_name）。"""

        bucket: Dict[Tuple[str, str], List[Skill]] = {}
        for skill in skills:
            bucket.setdefault((skill.namespace, skill.skill_name), []).append(skill)

        for (namespace, skill_name), members in bucket.items():
            if len(members) <= 1:
                continue
            conflicts = [
                {"space_id": it.space_id, "source_id": it.source_id, "locator": it.locator}
                for it in members
            ]
            raise FrameworkError(
                code="SKILL_DUPLICATE_NAME",
                message="Duplicate skill found in namespace.",
                details={"namespace": namespace, "skill_name": skill_name, "conflicts": conflicts},
            )

    def scan(self, *, force_refresh: bool = False) -> ScanReport:
        """执行 skills scan 并返回 ScanReport（可选强制刷新）。"""
        return _scan(self, force_refresh=force_refresh)

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
        """按 skill 路径启用/禁用（主要用于 Studio/UI 层的运行态过滤）。"""

        p = Path(skill_path).resolve()
        if p not in self._skills_by_path:
            raise UserError(f"未扫描到的 skill：{p}")
        if enabled:
            self._disabled_paths.discard(p)
        else:
            self._disabled_paths.add(p)

    def _raise_space_not_configured(self, mention) -> None:
        """抛出 space 未配置错误。"""

        raise FrameworkError(
            code="SKILL_SPACE_NOT_CONFIGURED",
            message="Skill space is not configured or disabled.",
            details={
                "mention": mention.mention_text,
                "namespace": mention.namespace,
            },
        )

    def resolve_mentions(self, text: str) -> List[Tuple[Skill, SkillMention]]:
        """解析文本中的 mentions，并返回命中的 skills 列表（保序）。"""
        return _resolve_mentions(self, text)

    def render_injected_skill(self, skill: Skill, *, source: str, mention_text: Optional[str] = None) -> str:
        """渲染注入到 prompt 的 skill 文本（用于 mention 注入）。"""
        return _render_injected_skill(self, skill, source=source, mention_text=mention_text)

    def close(self) -> None:
        """
        释放运行态创建的 source clients（例如 redis 连接）。

        约束：
        - 仅关闭运行态内部创建/缓存的 clients（`_runtime_source_clients`）；
        - 注入的 clients（`_source_clients`）由调用方管理，不在此处关闭。
        """

        clients = list((self._runtime_source_clients or {}).values())
        self._runtime_source_clients.clear()
        for client in clients:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def __enter__(self) -> "SkillsManager":
        """上下文管理器入口（返回 self）。"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """上下文管理器退出：关闭运行态资源。"""
        _ = (exc_type, exc, tb)
        self.close()
