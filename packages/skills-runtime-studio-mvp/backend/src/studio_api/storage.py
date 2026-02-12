from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from studio_api.skills_overlay import write_session_skills_overlay
from studio_api.timeutil import now_rfc3339


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    created_at: str
    title: Optional[str]
    updated_at: str
    runs_count: int


class FileStorage:
    """
    Studio MVP 的文件级存储（最小实现）。

    目录结构（workspace_root 下）：
    - `.skills_runtime_sdk/sessions/<session_id>/session.json`
    - `.skills_runtime_sdk/sessions/<session_id>/skills.json`
    - `.skills_runtime_sdk/sessions/<session_id>/skills_overlay.yaml`
    - `.skills_runtime_sdk/runs/<run_id>/events.jsonl`（由 Agent 写入）
    - `.skills_runtime_sdk/runs/<run_id>/run.json`（由 Studio 写入）
    """

    def __init__(self, *, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def _sdk_dir(self) -> Path:
        return (self.workspace_root / ".skills_runtime_sdk").resolve()

    def sessions_root(self) -> Path:
        p = (self._sdk_dir() / "sessions").resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def runs_root(self) -> Path:
        p = (self._sdk_dir() / "runs").resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def generated_skills_root(self) -> Path:
        p = (self._sdk_dir() / "skills").resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def session_dir(self, session_id: str) -> Path:
        return (self.sessions_root() / session_id).resolve()

    def run_dir(self, run_id: str) -> Path:
        return (self.runs_root() / run_id).resolve()

    def _read_json(self, path: Path) -> Dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def _write_json(self, path: Path, obj: Dict[str, Any]) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def _default_roots(self) -> List[str]:
        return [str(self.generated_skills_root())]

    def create_session(self, *, title: Optional[str], skills_roots: Optional[List[str]]) -> SessionRecord:
        sid = f"sess_{uuid.uuid4().hex}"
        created_at = now_rfc3339()
        updated_at = created_at

        roots = self._default_roots() if skills_roots is None else list(skills_roots)
        explicit_empty = bool(skills_roots is not None and len(skills_roots) == 0)

        sdir = self.session_dir(sid)
        sdir.mkdir(parents=True, exist_ok=True)

        self._write_json(
            sdir / "session.json",
            {
                "session_id": sid,
                "created_at": created_at,
                "title": title,
                "updated_at": updated_at,
                "runs_count": 0,
            },
        )
        self._write_json(
            sdir / "skills.json",
            {
                "roots": roots,
                "disabled_paths": [],
                "explicit_empty": explicit_empty,
                "mode": "explicit",
            },
        )

        write_session_skills_overlay(session_dir=sdir, roots=roots)
        return SessionRecord(
            session_id=sid,
            created_at=created_at,
            title=title,
            updated_at=updated_at,
            runs_count=0,
        )

    def list_sessions(self) -> List[SessionRecord]:
        out: List[SessionRecord] = []
        root = self.sessions_root()
        for sdir in sorted(root.iterdir(), key=lambda p: p.name):
            if not sdir.is_dir():
                continue
            session_json = sdir / "session.json"
            if not session_json.exists():
                continue
            obj = self._read_json(session_json)
            out.append(
                SessionRecord(
                    session_id=str(obj.get("session_id") or sdir.name),
                    created_at=str(obj.get("created_at") or ""),
                    title=obj.get("title") if isinstance(obj.get("title"), str) or obj.get("title") is None else None,
                    updated_at=str(obj.get("updated_at") or obj.get("created_at") or ""),
                    runs_count=int(obj.get("runs_count") or 0),
                )
            )
        # 新的在前（updated_at 为空时 fallback created_at）
        def _key(it: SessionRecord) -> str:
            return it.updated_at or it.created_at or ""

        return sorted(out, key=_key, reverse=True)

    def delete_session(self, session_id: str) -> bool:
        sdir = self.session_dir(session_id)
        if not sdir.exists():
            return False

        # 清理 session 目录
        shutil.rmtree(sdir, ignore_errors=True)

        # 清理关联 runs（best-effort）
        for rdir in self.runs_root().iterdir():
            if not rdir.is_dir():
                continue
            run_json = rdir / "run.json"
            if not run_json.exists():
                continue
            try:
                obj = self._read_json(run_json)
            except Exception:
                continue
            if str(obj.get("session_id") or "") == session_id:
                shutil.rmtree(rdir, ignore_errors=True)

        return True

    def get_skills_config(self, session_id: str) -> Dict[str, Any]:
        sdir = self.session_dir(session_id)
        p = sdir / "skills.json"
        if not p.exists():
            raise FileNotFoundError(str(p))
        return self._read_json(p)

    def update_skills_config(self, session_id: str, cfg: Dict[str, Any]) -> None:
        sdir = self.session_dir(session_id)
        if not sdir.exists():
            raise FileNotFoundError(str(sdir))
        self._write_json(sdir / "skills.json", cfg)
        roots = cfg.get("roots") if isinstance(cfg, dict) else None
        roots_list = [str(r).strip() for r in (roots or []) if str(r).strip()]
        write_session_skills_overlay(session_dir=sdir, roots=roots_list)

    def ensure_skills_roots_configured(self, session_id: str) -> Dict[str, Any]:
        """
        回填 roots（用于兼容旧/异常 session）。

        规则：
        - roots 缺失或为空
        - 且未显式声明 explicit_empty=true
        -> 回填为默认 roots（generated root）
        """

        cfg = self.get_skills_config(session_id)
        roots = cfg.get("roots")
        explicit_empty = bool(cfg.get("explicit_empty") is True)
        roots_missing_or_empty = roots is None or (isinstance(roots, list) and len(roots) == 0)
        if roots_missing_or_empty and (not explicit_empty):
            cfg["roots"] = list(self._default_roots())
            cfg.setdefault("disabled_paths", [])
            cfg.setdefault("mode", "explicit")
            self.update_skills_config(session_id, cfg)
        return cfg

    def skills_overlay_path(self, session_id: str) -> Path:
        return (self.session_dir(session_id) / "skills_overlay.yaml").resolve()

    def write_run_record(self, *, run_id: str, session_id: str) -> Path:
        rdir = self.run_dir(run_id)
        rdir.mkdir(parents=True, exist_ok=True)
        p = rdir / "run.json"
        self._write_json(
            p,
            {
                "run_id": run_id,
                "session_id": session_id,
                "created_at": now_rfc3339(),
            },
        )
        return p

