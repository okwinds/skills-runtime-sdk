from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from studio_api.skills_overlay import write_session_skills_overlay
from studio_api.timeutil import now_rfc3339

logger = logging.getLogger(__name__)


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
        self._session_locks: Dict[str, threading.Lock] = {}
        self._session_locks_guard = threading.Lock()

    def _get_session_lock(self, session_id: str) -> threading.Lock:
        """返回 session 级互斥锁，避免 run 计数的进程内竞争写。"""

        key = str(session_id)
        with self._session_locks_guard:
            lock = self._session_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[key] = lock
            return lock

    def normalize_filesystem_source(self, raw_path: str) -> str:
        """
        规范化并校验 filesystem source。

        规则：
        - 相对路径按 workspace_root 解析；
        - 绝对/相对路径最终都必须位于 workspace_root 内。
        """

        text = str(raw_path or "").strip()
        if not text:
            raise ValueError("filesystem_source 不能为空")
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = (self.workspace_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("invalid filesystem_source: path must stay within workspace_root") from exc
        return str(candidate)

    def normalize_filesystem_sources(self, filesystem_sources: Optional[List[str]]) -> List[str]:
        """批量规范化 filesystem sources，忽略空白项。"""

        out: List[str] = []
        for raw in filesystem_sources or []:
            if not isinstance(raw, str):
                continue
            text = raw.strip()
            if not text:
                continue
            out.append(self.normalize_filesystem_source(text))
        return out

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
    
    @staticmethod
    def _ensure_under_root(*, root: Path, path: Path, kind: str) -> None:
        root2 = Path(root).resolve()
        path2 = Path(path).resolve()
        try:
            path2.relative_to(root2)
        except ValueError as exc:
            raise ValueError(f"invalid {kind}: path traversal detected") from exc

    def generated_skills_root(self) -> Path:
        p = (self._sdk_dir() / "skills").resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def session_dir(self, session_id: str) -> Path:
        root = self.sessions_root()
        p = (root / str(session_id)).resolve()
        self._ensure_under_root(root=root, path=p, kind="session_id")
        return p

    def run_dir(self, run_id: str) -> Path:
        root = self.runs_root()
        p = (root / str(run_id)).resolve()
        self._ensure_under_root(root=root, path=p, kind="run_id")
        return p

    def _read_json(self, path: Path) -> Dict[str, Any]:
        """读取 JSON 文件并返回 dict。"""

        obj = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError(f"invalid json object: {path}")
        return obj

    def _read_json_checked(self, path: Path, *, kind: str) -> Dict[str, Any]:
        """
        读取并校验 JSON 元数据。

        说明：
        - 对外统一抛 `ValueError`，避免 API 层把 `JSONDecodeError` 直接暴露成 500。
        """

        try:
            return self._read_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid {kind}: {path.name}") from exc

    def _atomic_write_text(self, path: Path, text: str) -> None:
        """
        以“临时文件 + rename”方式原子写文本。

        说明：
        - 目标目录与临时文件保持同目录，确保 `rename` 具备原子性；
        - `fsync` 仅做 best-effort，避免平台差异放大失败面。
        """

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

    def _write_json(self, path: Path, obj: Dict[str, Any]) -> None:
        """原子写入 JSON 元数据。"""

        self._atomic_write_text(Path(path), json.dumps(obj, ensure_ascii=False))

    def _coerce_non_negative_int(self, raw: object, *, field_name: str) -> int:
        """
        将元数据字段稳定转换为非负整数。

        说明：
        - 缺字段或空串按 0 处理，兼容旧 session 元数据；
        - bool 明确拒绝，避免 `True/False` 被误当成 `1/0`。
        """

        if raw in (None, ""):
            return 0
        if isinstance(raw, bool):
            raise ValueError(f"invalid {field_name}")
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {field_name}") from exc
        if value < 0:
            raise ValueError(f"invalid {field_name}")
        return value

    def _session_record_from_obj(self, *, session_dir: Path, obj: Dict[str, Any]) -> SessionRecord:
        """
        把 `session.json` 稳定映射为 `SessionRecord`。

        约束：
        - `runs_count` 不合法时整条 session 视为坏元数据并跳过；
        - 其余展示字段做 fail-closed 降级，避免单字段脏值把列表 API 打挂。
        """

        raw_session_id = obj.get("session_id")
        session_id = raw_session_id.strip() if isinstance(raw_session_id, str) and raw_session_id.strip() else session_dir.name

        raw_created_at = obj.get("created_at")
        created_at = raw_created_at if isinstance(raw_created_at, str) else ""

        raw_updated_at = obj.get("updated_at")
        updated_at = raw_updated_at if isinstance(raw_updated_at, str) else created_at

        raw_title = obj.get("title")
        title = raw_title if isinstance(raw_title, str) else None

        return SessionRecord(
            session_id=session_id,
            created_at=created_at,
            title=title,
            updated_at=updated_at,
            runs_count=self._coerce_non_negative_int(obj.get("runs_count"), field_name="runs_count"),
        )

    def _default_filesystem_sources(self) -> List[str]:
        """
        Studio MVP 默认提供一个“generated skills”目录（filesystem source root），开箱即用。
        """

        return [str(self.generated_skills_root())]

    def create_session(self, *, title: Optional[str], filesystem_sources: Optional[List[str]]) -> SessionRecord:
        sid = f"sess_{uuid.uuid4().hex}"
        created_at = now_rfc3339()
        updated_at = created_at

        sources = (
            self._default_filesystem_sources()
            if filesystem_sources is None
            else self.normalize_filesystem_sources(filesystem_sources)
        )

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
                "filesystem_sources": sources,
                "disabled_paths": [],
            },
        )

        write_session_skills_overlay(session_dir=sdir, filesystem_sources=sources)
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
            try:
                obj = self._read_json_checked(session_json, kind="session metadata")
                record = self._session_record_from_obj(session_dir=sdir, obj=obj)
            except ValueError:
                logger.warning("skip corrupted session metadata: %s", session_json, exc_info=True)
                continue
            out.append(record)
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
        return self._read_json_checked(p, kind="skills metadata")

    def update_skills_config(self, session_id: str, cfg: Dict[str, Any]) -> None:
        sdir = self.session_dir(session_id)
        if not sdir.exists():
            raise FileNotFoundError(str(sdir))
        cfg = dict(cfg)
        cfg["filesystem_sources"] = self.normalize_filesystem_sources(cfg.get("filesystem_sources"))
        self._write_json(sdir / "skills.json", cfg)
        sources = cfg.get("filesystem_sources") if isinstance(cfg, dict) else None
        sources_list = [str(r).strip() for r in (sources or []) if str(r).strip()]
        write_session_skills_overlay(session_dir=sdir, filesystem_sources=sources_list)

    def skills_overlay_path(self, session_id: str) -> Path:
        return (self.session_dir(session_id) / "skills_overlay.yaml").resolve()

    def write_run_record(self, *, run_id: str, session_id: str) -> Path:
        """写入 run 记录，并同步维护所属 session 的 `runs_count`。"""

        with self._get_session_lock(session_id):
            rdir = self.run_dir(run_id)
            rdir.mkdir(parents=True, exist_ok=True)
            p = rdir / "run.json"
            session_json = self.session_dir(session_id) / "session.json"
            if not session_json.exists():
                raise FileNotFoundError(str(session_json))
            session_obj = self._read_json_checked(session_json, kind="session metadata")
            session_obj["runs_count"] = self._coerce_non_negative_int(
                session_obj.get("runs_count"),
                field_name="runs_count",
            ) + 1
            session_obj["updated_at"] = now_rfc3339()
            self._write_json(
                p,
                {
                    "run_id": run_id,
                    "session_id": session_id,
                    "created_at": now_rfc3339(),
                },
            )
            self._write_json(session_json, session_obj)
            return p
