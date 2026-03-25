from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List

import yaml


def _atomic_write_text(path: Path, text: str) -> None:
    """以临时文件 + rename 方式原子写 overlay 文本。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            with contextlib.suppress(OSError):
                os.fsync(f.fileno())
        tmp.replace(target)
    finally:
        with contextlib.suppress(FileNotFoundError, OSError):
            if tmp.exists():
                tmp.unlink()


def skills_config_from_filesystem_sources(*, filesystem_sources: List[str]) -> Dict[str, Any]:
    """
    将 Studio MVP 的 filesystem sources 列表转换为 Skills（spaces/sources）配置。

    约束：
    - filesystem_sources 每一项对应一个 filesystem source
    - 所有 sources 聚合到一个固定 space：namespace=web:mvp（对齐文档与 demo 约定）
    """

    cleaned_roots: List[str] = []
    for r in filesystem_sources or []:
        rr = str(r or "").strip()
        if rr:
            cleaned_roots.append(rr)

    source_ids: List[str] = []
    sources: List[Dict[str, Any]] = []
    for idx, root in enumerate(cleaned_roots):
        sid = f"web-fs-{idx}"
        source_ids.append(sid)
        sources.append({"id": sid, "type": "filesystem", "options": {"root": root}})

    spaces: List[Dict[str, Any]] = []
    if source_ids:
        spaces.append(
            {
                "id": "space-web-mvp",
                "namespace": "web:mvp",
                "sources": source_ids,
                "enabled": True,
            }
        )

    return {
        "spaces": spaces,
        "sources": sources,
        "injection": {"max_bytes": 65536},
    }


def write_session_skills_overlay(*, session_dir: Path, filesystem_sources: List[str]) -> Path:
    """
    为某个 session 写入 skills overlay YAML，并返回路径。

    参数：
    - session_dir：session 目录（`.skills_runtime_sdk/sessions/<session_id>`）
    - filesystem_sources：session 生效的 filesystem sources（每项是一个 root path 字符串）
    """

    cfg = skills_config_from_filesystem_sources(filesystem_sources=filesystem_sources)
    overlay_path = (Path(session_dir) / "skills_overlay.yaml").resolve()

    overlay_obj: Dict[str, Any] = {"config_version": 1, "skills": cfg}
    _atomic_write_text(
        overlay_path,
        yaml.safe_dump(overlay_obj, sort_keys=False, allow_unicode=True),
    )
    return overlay_path
