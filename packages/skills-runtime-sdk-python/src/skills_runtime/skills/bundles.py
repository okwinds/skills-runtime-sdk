"""
Skills bundles（Phase 3：actions / references）的最小运行时实现。

对齐 OpenSpec change：skills-redis-bundles-actions-refread-perf

目标：
- 支持从远端（例如 Redis）获取 zip bundle，并安全解压到 runtime-owned cache；
- 严格 fail-closed：路径逃逸、symlink、unexpected top-level 目录一律拒绝；
- 仅允许 `actions/**` + `references/**` 两类资产（最小集合）。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
import shutil
import stat
import uuid
import zipfile
from typing import Iterable, Optional, Sequence

from skills_runtime.core.errors import FrameworkError


_ALLOWED_TOP_LEVEL_DIRS = {"actions", "references"}
_DEFAULT_MAX_FILES = 4096
_DEFAULT_MAX_EXTRACTED_BYTES_MULTIPLIER = 16
_DEFAULT_MAX_SINGLE_FILE_BYTES_MULTIPLIER = 8


@dataclass(frozen=True)
class ExtractedBundle:
    """已解压 bundle 的运行时投影。"""

    bundle_sha256: str
    bundle_root: Path


def _is_sha256_hex(value: str) -> bool:
    """判断字符串是否为 64 位 sha256 hex。"""

    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _zipinfo_is_symlink(info: zipfile.ZipInfo) -> bool:
    """
    判断 zip entry 是否为 symlink（Unix mode）。

    说明：
    - zipfile 并未提供直接 API；常见做法是检查 external_attr 的高 16 位。
    """

    mode = (int(getattr(info, "external_attr", 0)) >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _validate_zip_entry_name(name: str) -> PurePosixPath:
    """
    校验 zip entry name 的安全性并返回规范化的 PurePosixPath（posix 分隔）。

    约束（fail-closed）：
    - 禁止空路径
    - 禁止反斜杠（Windows 形态）
    - 禁止绝对路径（以 / 开头）
    - 禁止 `..` 段
    - 顶层目录必须在允许集合内
    """

    raw = str(name or "")
    if not raw or raw in {".", "/"}:
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle zip entry is invalid.",
            details={"reason": "empty_name"},
        )
    if "\\" in raw:
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle zip entry is invalid.",
            details={"reason": "backslash_in_name", "name": raw},
        )
    if raw.startswith("/"):
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle zip entry is invalid.",
            details={"reason": "absolute_path", "name": raw},
        )

    p = PurePosixPath(raw)
    parts = [x for x in p.parts if x not in {"", "."}]
    if not parts:
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle zip entry is invalid.",
            details={"reason": "empty_parts", "name": raw},
        )
    if any(part == ".." for part in parts):
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle zip entry is invalid.",
            details={"reason": "dotdot_segment", "name": raw},
        )

    top = parts[0]
    if top not in _ALLOWED_TOP_LEVEL_DIRS:
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle contains unexpected top-level entries.",
            details={"reason": "unexpected_top_level", "top_level": top, "allowed": sorted(_ALLOWED_TOP_LEVEL_DIRS)},
        )

    return PurePosixPath(*parts)


def _iter_safe_zip_members(zf: zipfile.ZipFile) -> Iterable[tuple[zipfile.ZipInfo, PurePosixPath]]:
    """遍历 zip members 并进行安全校验（路径与 symlink）。"""

    for info in zf.infolist():
        # zipfile 目录通常以 / 结尾；无论目录与否都先校验 name
        path = _validate_zip_entry_name(info.filename)
        if _zipinfo_is_symlink(info):
            raise FrameworkError(
                code="SKILL_BUNDLE_INVALID",
                message="Skill bundle contains symlink entries, which are not allowed.",
                details={"name": info.filename},
            )
        yield info, path


def _copy_with_budget(
    *,
    src,  # zipfile.ZipExtFile
    dst,  # BinaryIO
    max_bytes: int,
    budget_reason: str,
) -> int:
    """
    将 src copy 到 dst，并强制限制最多写入 max_bytes。

    说明：
    - 这是对 zip header（ZipInfo.file_size）的防御性兜底：即使 header 被伪造也不会导致无上限落盘。
    - 超限时 fail-closed；由上层调用方负责清理部分产物（通常为 tmp dir）。
    """

    max_bytes = max(1, int(max_bytes))
    written = 0
    while True:
        remaining = max_bytes - written
        if remaining <= 0:
            # 尝试读 1 字节以确认是否还有内容；若有则判定超限。
            if src.read(1):
                raise FrameworkError(
                    code="SKILL_BUNDLE_TOO_LARGE",
                    message="Skill bundle exceeds configured size budget.",
                    details={"reason": budget_reason, "max_bytes": max_bytes},
                )
            break

        chunk = src.read(min(64 * 1024, remaining))
        if not chunk:
            break
        dst.write(chunk)
        written += len(chunk)
    return written


def extract_zip_bundle_to_dir(
    *,
    bundle_bytes: bytes,
    dest_dir: Path,
    expected_sha256: str,
    max_bytes: int,
    max_extracted_bytes: Optional[int] = None,
    max_files: Optional[int] = None,
    max_single_file_bytes: Optional[int] = None,
    allowed_top_level_dirs: Sequence[str] = ("actions", "references"),
) -> None:
    """
    将 zip bundle 安全解压到指定目录（dest_dir 必须已存在且为空或可覆盖）。

    参数：
    - bundle_bytes：zip bytes（必须为 bytes）
    - dest_dir：目标目录（绝对路径建议；由调用方保证 runtime-owned）
    - expected_sha256：期望的内容指纹（用于防止 TOCTOU/缓存错配）
    - max_bytes：bundle bytes 预算（>=1）
    - max_extracted_bytes：解压后总字节预算（None 时按默认倍率从 max_bytes 推导）
    - max_files：解压文件数预算（None 时使用默认值）
    - max_single_file_bytes：单文件解压字节预算（None 时按默认倍率从 max_bytes 推导）
    - allowed_top_level_dirs：允许的顶层目录集合（最小集合默认 actions/references）
    """

    if not isinstance(bundle_bytes, (bytes, bytearray)):
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle bytes are invalid.",
            details={"reason": "not_bytes"},
        )
    data = bytes(bundle_bytes)

    max_bytes = max(1, int(max_bytes))
    if len(data) > max_bytes:
        raise FrameworkError(
            code="SKILL_BUNDLE_TOO_LARGE",
            message="Skill bundle exceeds configured size budget.",
            details={"bundle_bytes": len(data), "max_bytes": max_bytes},
        )

    if max_extracted_bytes is None:
        max_extracted_bytes = max_bytes * _DEFAULT_MAX_EXTRACTED_BYTES_MULTIPLIER
    max_extracted_bytes = max(1, int(max_extracted_bytes))

    if max_single_file_bytes is None:
        max_single_file_bytes = max_bytes * _DEFAULT_MAX_SINGLE_FILE_BYTES_MULTIPLIER
    max_single_file_bytes = max(1, int(max_single_file_bytes))

    if max_files is None:
        max_files = _DEFAULT_MAX_FILES
    max_files = max(1, int(max_files))

    sha = hashlib.sha256(data).hexdigest()
    if not _is_sha256_hex(expected_sha256):
        raise FrameworkError(
            code="SKILL_BUNDLE_CONTRACT_INVALID",
            message="Skill bundle contract is invalid.",
            details={"field": "bundle_sha256"},
        )
    if sha != expected_sha256:
        raise FrameworkError(
            code="SKILL_BUNDLE_FINGERPRINT_MISMATCH",
            message="Skill bundle fingerprint does not match bundle bytes.",
            details={"expected": expected_sha256, "actual": sha},
        )

    allowed = set(str(x) for x in (allowed_top_level_dirs or ()))
    if allowed != set(_ALLOWED_TOP_LEVEL_DIRS):
        # 目前仅支持最小集合；避免未来不小心放开目录导致攻击面扩大
        raise FrameworkError(
            code="SKILL_BUNDLE_CONTRACT_INVALID",
            message="Skill bundle allowed top-level dirs configuration is invalid in current version.",
            details={"allowed_top_level_dirs": sorted(allowed)},
        )

    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle is not a valid zip archive.",
            details={"reason": str(exc)},
        ) from exc

    with zf:
        extracted_bytes = 0
        extracted_files = 0
        for info, rel in _iter_safe_zip_members(zf):
            # skip pure directory entries (after validation)
            if info.is_dir():
                continue

            extracted_files += 1
            if extracted_files > max_files:
                raise FrameworkError(
                    code="SKILL_BUNDLE_TOO_LARGE",
                    message="Skill bundle exceeds configured size budget.",
                    details={"reason": "max_files", "files": extracted_files, "max_files": max_files},
                )

            declared_size = int(getattr(info, "file_size", 0) or 0)
            if declared_size > max_single_file_bytes:
                raise FrameworkError(
                    code="SKILL_BUNDLE_TOO_LARGE",
                    message="Skill bundle exceeds configured size budget.",
                    details={"reason": "max_single_file_bytes", "file_bytes": declared_size, "max_single_file_bytes": max_single_file_bytes, "name": info.filename},
                )
            if extracted_bytes + declared_size > max_extracted_bytes:
                raise FrameworkError(
                    code="SKILL_BUNDLE_TOO_LARGE",
                    message="Skill bundle exceeds configured size budget.",
                    details={"reason": "max_extracted_bytes", "extracted_bytes": extracted_bytes, "next_file_bytes": declared_size, "max_extracted_bytes": max_extracted_bytes, "name": info.filename},
                )

            target = (dest_dir / Path(rel.as_posix())).resolve()
            root = dest_dir.resolve()
            if not target.is_relative_to(root):
                raise FrameworkError(
                    code="SKILL_BUNDLE_INVALID",
                    message="Skill bundle extraction would escape destination directory.",
                    details={"name": info.filename, "target": str(target), "dest_dir": str(root)},
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                remaining_total = max_extracted_bytes - extracted_bytes
                if remaining_total < 1:
                    raise FrameworkError(
                        code="SKILL_BUNDLE_TOO_LARGE",
                        message="Skill bundle exceeds configured size budget.",
                        details={"reason": "max_extracted_bytes", "extracted_bytes": extracted_bytes, "max_extracted_bytes": max_extracted_bytes},
                    )
                copy_budget = min(max_single_file_bytes, remaining_total)
                budget_reason = "max_extracted_bytes" if remaining_total <= max_single_file_bytes else "max_single_file_bytes"
                wrote = _copy_with_budget(src=src, dst=dst, max_bytes=copy_budget, budget_reason=budget_reason)
                extracted_bytes += wrote


def ensure_extracted_bundle(
    *,
    cache_root: Path,
    bundle_sha256: str,
    bundle_bytes: bytes,
    max_bytes: int,
    max_extracted_bytes: Optional[int] = None,
    max_files: Optional[int] = None,
    max_single_file_bytes: Optional[int] = None,
) -> ExtractedBundle:
    """
    确保 bundle 已解压到 cache_root 下的 `<sha256>/`，并返回其路径。

    约束：
    - 以 sha256 作为 content-addressed cache key；
    - 使用临时目录 + 原子 rename，避免并发/中断导致半成品目录被复用；
    - 若目标目录已存在，直接复用（不会重复解压、也不会重复读取 redis）。
    """

    if not _is_sha256_hex(bundle_sha256):
        raise FrameworkError(
            code="SKILL_BUNDLE_CONTRACT_INVALID",
            message="Skill bundle contract is invalid.",
            details={"field": "bundle_sha256"},
        )

    cache_root = Path(cache_root).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    final_dir = (cache_root / bundle_sha256).resolve()

    # fast path：已存在则直接复用（runtime-owned；可被清理并重建）
    if final_dir.exists() and final_dir.is_dir():
        return ExtractedBundle(bundle_sha256=bundle_sha256, bundle_root=final_dir)

    tmp_dir = (cache_root / f".tmp.{bundle_sha256}.{uuid.uuid4().hex[:10]}").resolve()
    tmp_dir.mkdir(parents=True, exist_ok=False)
    try:
        extract_zip_bundle_to_dir(
            bundle_bytes=bundle_bytes,
            dest_dir=tmp_dir,
            expected_sha256=bundle_sha256,
            max_bytes=max_bytes,
            max_extracted_bytes=max_extracted_bytes,
            max_files=max_files,
            max_single_file_bytes=max_single_file_bytes,
            allowed_top_level_dirs=("actions", "references"),
        )

        try:
            tmp_dir.replace(final_dir)
        except FileExistsError:
            # 并发情形：另一个进程/线程已完成解压；丢弃本次 tmp 并复用最终目录
            pass
    finally:
        # 若 replace 成功，tmp_dir 已不存在；若失败/并发，清理残留 tmp_dir
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not final_dir.exists() or not final_dir.is_dir():
        raise FrameworkError(
            code="SKILL_BUNDLE_INVALID",
            message="Skill bundle extraction failed.",
            details={"bundle_sha256": bundle_sha256, "cache_root": str(cache_root)},
        )

    return ExtractedBundle(bundle_sha256=bundle_sha256, bundle_root=final_dir)
