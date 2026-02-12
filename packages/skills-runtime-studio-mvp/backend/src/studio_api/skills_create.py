from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from studio_api.errors import http_error
from studio_api.skill_scaffold import validate_skill_name, write_skill
from studio_api.storage import FileStorage


class CreateSkillReq(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    body_markdown: str = ""
    title: Optional[str] = None
    target_root: Optional[str] = None


def _clean_roots(cfg: dict[str, Any]) -> List[str]:
    roots_raw = cfg.get("roots") or []
    roots: List[str] = []
    for r in roots_raw:
        if isinstance(r, str):
            rr = r.strip()
            if rr:
                roots.append(rr)
    return roots


def _target_root_allowed(*, target_root: str, roots: List[str]) -> bool:
    """
    判断 target_root 是否属于 roots（支持 path resolve 等价）。

    参数：
    - target_root：用户请求写入的目录
    - roots：session 配置的 roots（字符串列表）
    """

    if target_root in roots:
        return True
    try:
        t_resolved = str(Path(target_root).resolve())
    except Exception:
        return False
    for r in roots:
        try:
            if str(Path(r).resolve()) == t_resolved:
                return True
        except Exception:
            continue
    return False


def bind_create_skill_router(*, storage: FileStorage) -> APIRouter:
    """
    绑定“创建 skill”路由到给定 storage（便于测试注入 workspace_root）。

    参数：
    - storage：文件级存储实例
    """

    router = APIRouter()

    @router.post("/studio/api/v1/sessions/{session_id}/skills", status_code=201)
    async def create_skill(session_id: str, body: CreateSkillReq = Body(...)) -> dict[str, Any]:
        """
        在 session 的某个 skills root 下创建一个文件级 Skill（落盘 SKILL.md）。
        """

        try:
            cfg = storage.ensure_skills_roots_configured(session_id)
        except FileNotFoundError:
            raise http_error("not_found", "session not found", status_code=404, details={"session_id": session_id})

        roots = _clean_roots(cfg)
        if not roots:
            raise http_error(
                "validation_error",
                "skills roots not configured for session",
                status_code=400,
                details={"session_id": session_id},
            )

        target_root = (body.target_root or "").strip() or roots[0]
        if not _target_root_allowed(target_root=target_root, roots=roots):
            raise http_error(
                "validation_error",
                "target_root must be one of session roots",
                status_code=400,
                details={"target_root": target_root, "roots": roots},
            )

        try:
            validate_skill_name(body.name)
        except ValueError as e:
            raise http_error("validation_error", str(e), status_code=400, details={"field": "name"})

        try:
            result = write_skill(
                root_dir=Path(target_root),
                skill_name=body.name,
                description=body.description,
                title=body.title,
                body_markdown=body.body_markdown,
            )
        except FileExistsError as e:
            raise http_error("conflict", str(e), status_code=409, details={})
        except ValueError as e:
            raise http_error("validation_error", str(e), status_code=400, details={})

        return {
            "ok": True,
            "root": target_root,
            "skill_dir": str(result.skill_dir),
            "skill_md": str(result.skill_md),
        }

    return router
