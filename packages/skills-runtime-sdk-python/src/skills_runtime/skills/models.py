"""
Skills 数据模型（Phase 2）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/skills.md`
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Set

from skills_runtime.core.errors import FrameworkIssue


@dataclass(frozen=True)
class Skill:
    """
    Skill 结构（加载后的稳定表示）。

    字段：
    - name/description：来自 frontmatter（必填）
    - space_id/source_id/namespace：来源空间与命名信息（spaces/sources）
    - path：SKILL.md 的 canonical 路径（可选，非 filesystem 时可为空）
    - locator：跨 source 的稳定定位符（path/key/row id）
    - body_size：正文字节数（metadata-only 阶段可用，未知可为 None）
    - body_loader：懒加载正文函数（inject 时调用）
    - required_env_vars：来自 `agents/openai.yaml`（Phase 2：仅支持 env_var dependencies）
    - metadata：frontmatter 的其它字段（fail-open）
    """

    space_id: str
    source_id: str
    namespace: str
    skill_name: str
    description: str
    locator: str
    path: Optional[Path]
    body_size: Optional[int]
    body_loader: Callable[[], str]
    required_env_vars: List[str]
    metadata: Dict[str, Any]
    scope: Optional[str] = None  # repo|user|system（Phase 2 可选）

    def to_metadata_dict(self) -> Dict[str, object]:
        """
        将 Skill 投影为可 JSON 序列化的 metadata-only 视图（不含正文）。

        约束（对齐 `docs/specs/skills-runtime-sdk/docs/skills-scan-report-jsonable.md`）：
        - 不得输出 `body_loader`
        - 必须移除 `metadata.body_markdown`（如存在）
        - 所有字段值必须经过 JSON 清洗（保证 `json.dumps(..., allow_nan=False)` 不抛）
        """

        metadata_obj: Dict[str, Any] = {}
        if isinstance(self.metadata, Mapping):
            metadata_obj = dict(self.metadata)
        else:
            # fail-open：非 dict 的 metadata 仍需输出 object
            metadata_obj = {"value": self.metadata}

        metadata_obj.pop("body_markdown", None)

        return {
            "space_id": _json_sanitize(self.space_id),
            "source_id": _json_sanitize(self.source_id),
            "namespace": _json_sanitize(self.namespace),
            "skill_name": _json_sanitize(self.skill_name),
            "description": _json_sanitize(self.description),
            "locator": _json_sanitize(self.locator),
            "path": str(self.path) if self.path is not None else None,
            "body_size": _json_sanitize(self.body_size),
            "required_env_vars": _json_sanitize(list(self.required_env_vars)),
            "metadata": _json_sanitize(metadata_obj),
            "scope": _json_sanitize(self.scope),
        }

@dataclass(frozen=True)
class ScanReport:
    """Skills 扫描报告（metadata-only）。"""

    scan_id: str
    skills: List[Skill]
    errors: List[FrameworkIssue]
    warnings: List[FrameworkIssue]
    stats: Dict[str, int]

    def to_jsonable(self) -> Dict[str, object]:
        """
        将 ScanReport 投影为可 JSON 序列化的只读视图（fail-open）。

        输出 schema 对齐：
        - `docs/specs/skills-runtime-sdk/docs/skills-scan-report-jsonable.md` §3.2

        约束：
        - 不读取 Skill 正文（不得调用 `Skill.body_loader`）
        - 对 `errors/warnings/details/metadata` 做递归 JSON 清洗（含 NaN/Inf、循环与最大深度保护）
        """

        skills_json = [s.to_metadata_dict() for s in (self.skills or [])]
        errors_json = [_issue_to_jsonable(it) for it in (self.errors or [])]
        warnings_json = [_issue_to_jsonable(it) for it in (self.warnings or [])]

        stats_json: Dict[str, int] = {}
        if isinstance(self.stats, Mapping):
            for k, v in self.stats.items():
                key = str(k)
                stats_json[key] = _coerce_int(v)

        return {
            "scan_id": _json_sanitize(self.scan_id),
            "skills": skills_json,
            "errors": errors_json,
            "warnings": warnings_json,
            "stats": stats_json,
        }

    def __len__(self) -> int:
        """兼容旧接口：`len(report)` 等价于 skills 数量。"""

        return len(self.skills)

    def __iter__(self):
        """兼容旧接口：可迭代 skills。"""

        return iter(self.skills)

    def __getitem__(self, index: int) -> Skill:
        """兼容旧接口：下标访问 skills。"""

        return self.skills[index]


def _coerce_int(value: Any) -> int:
    """将任意值尽力转换为 int（fail-open；失败则返回 0）。"""

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_repr(value: Any) -> str:
    """对任意值返回稳定的 repr 字符串（fail-open）。"""

    try:
        return repr(value)
    except Exception:
        # 防御性兜底：任意对象的 __repr__ 可能抛出异常（用户自定义类）。
        return "<unreprable>"


def _safe_str(value: Any) -> str:
    """对任意值返回稳定的 str 字符串（fail-open）。"""

    try:
        return str(value)
    except Exception:
        # 防御性兜底：任意对象的 __str__ 可能抛出异常（用户自定义类）。
        return _safe_repr(value)


def _json_sanitize(value: Any, *, _depth: int = 0, _max_depth: int = 8, _stack: Optional[Set[int]] = None) -> Any:
    """
    将任意对象递归清洗为 JSON 兼容值（dict/list/str/int/float/bool/None）。

    关键规则：
    - float NaN/Inf 降级为字符串（避免 `allow_nan=False` 失败）
    - Path/bytes/Exception 做结构化降级
    - dict key 强制转为 string
    - set 转为排序后的 list（保证稳定输出）
    - 最大深度与循环引用保护
    """

    if _depth >= _max_depth:
        return "<max_depth_reached>"

    if value is None or isinstance(value, (bool, int, str)):
        return value

    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, bytes):
        digest = hashlib.sha256(value).hexdigest()
        return {"__type__": "bytes", "len": len(value), "sha256": digest}

    if isinstance(value, Exception):
        return {"__type__": "exception", "class": value.__class__.__name__, "message": _safe_str(value)}

    stack = _stack or set()
    value_id = id(value)
    if value_id in stack:
        return "<cycle>"

    if isinstance(value, (list, tuple)):
        stack.add(value_id)
        try:
            return [_json_sanitize(v, _depth=_depth + 1, _max_depth=_max_depth, _stack=stack) for v in value]
        finally:
            stack.discard(value_id)

    if isinstance(value, set):
        stack.add(value_id)
        try:
            items = [_json_sanitize(v, _depth=_depth + 1, _max_depth=_max_depth, _stack=stack) for v in value]

            def _sort_key(x: Any) -> str:
                """
                为集合元素生成稳定排序键，确保序列化结果可复现。

                规则：
                - 优先对 `_safe_str(x)` 的 UTF-8 字节做 SHA-256，返回 hexdigest；
                - 若上述过程发生异常，则回退到 `_safe_repr(x)`（仍返回 `str`）。

                参数：
                - x：已被 `_json_sanitize` 处理后的集合元素。

                返回：
                - 排序 key（`str`），用于 `sorted(..., key=_sort_key)`。

                异常：
                - 无（内部捕获所有异常并回退到 `_safe_repr`）。
                """
                try:
                    return hashlib.sha256(
                        _safe_str(x).encode("utf-8", errors="replace")
                    ).hexdigest()
                except Exception:
                    # 防御性兜底：任意对象的 __str__/__hash__ 可能抛出异常（用户自定义类）。
                    return _safe_repr(x)

            return sorted(items, key=_sort_key)
        finally:
            stack.discard(value_id)

    if isinstance(value, Mapping):
        stack.add(value_id)
        try:
            out: Dict[str, Any] = {}
            collisions: Dict[str, int] = {}
            for k, v in value.items():
                key = _safe_str(k)
                if key in out:
                    n = collisions.get(key, 0) + 1
                    collisions[key] = n
                    key = f"{key}__dup__{n}"
                out[key] = _json_sanitize(v, _depth=_depth + 1, _max_depth=_max_depth, _stack=stack)
            # 稳定 key 顺序（便于 golden diff）
            return {k: out[k] for k in sorted(out.keys())}
        finally:
            stack.discard(value_id)

    return _safe_repr(value)


def _issue_to_jsonable(issue: Any) -> Dict[str, object]:
    """
    将 errors/warnings 项投影为 IssueJsonable（fail-open）。

    规则：
    - 优先识别 `FrameworkIssue`（或同形对象）
    - 未识别时降级为 UNKNOWN_ISSUE，并把原对象 repr 放入 details.value
    """

    if isinstance(issue, FrameworkIssue):
        details = issue.details if isinstance(issue.details, Mapping) else {"value": issue.details}
        return {
            "code": _safe_str(issue.code),
            "message": _safe_str(issue.message),
            "details": _json_sanitize(details),
        }

    code = getattr(issue, "code", None)
    message = getattr(issue, "message", None)
    details = getattr(issue, "details", None)
    if isinstance(code, str) and isinstance(message, str):
        details_obj: Dict[str, Any]
        if isinstance(details, Mapping):
            details_obj = dict(details)
        else:
            details_obj = {"value": details}
        return {"code": code, "message": message, "details": _json_sanitize(details_obj)}

    if isinstance(issue, Mapping):
        raw_code = issue.get("code")
        raw_message = issue.get("message")
        raw_details = issue.get("details")
        if isinstance(raw_code, str) and isinstance(raw_message, str):
            details_obj2: Dict[str, Any]
            if isinstance(raw_details, Mapping):
                details_obj2 = dict(raw_details)
            else:
                details_obj2 = {"value": raw_details}
            return {"code": raw_code, "message": raw_message, "details": _json_sanitize(details_obj2)}

    return {
        "code": "UNKNOWN_ISSUE",
        "message": "Non-standard issue object.",
        "details": {"value": _json_sanitize(issue)},
    }
