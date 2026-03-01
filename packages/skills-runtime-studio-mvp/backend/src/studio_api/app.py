from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from skills_runtime.agent import Agent
from skills_runtime import bootstrap as agent_bootstrap
from skills_runtime.llm import ChatStreamEvent, FakeChatBackend, FakeChatCall
from skills_runtime.skills import SkillsManager
from skills_runtime.tools import ToolCall

from studio_api.approvals import ApprovalHub
from studio_api.envfile import load_dotenv_for_workspace
from studio_api.errors import http_error
from studio_api.example_skills import ensure_example_skills_installed
from studio_api.sse import stream_jsonl_as_sse
from studio_api.skills_create import bind_create_skill_router
from studio_api.skills_overlay import skills_config_from_filesystem_sources
from studio_api.storage import FileStorage
from studio_api.timeutil import now_rfc3339


def _resolve_workspace_root_from_env() -> Path:
    """
    解析 workspace_root。

    约定：
    - 若设置 `STUDIO_WORKSPACE_ROOT`，使用其作为 workspace root（测试/多实例隔离）。
    - 否则使用当前进程工作目录（dev.sh 会在 backend/ 下启动）。
    """

    import os

    env = (os.getenv("STUDIO_WORKSPACE_ROOT") or "").strip()
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


_WORKSPACE_ROOT = _resolve_workspace_root_from_env()
_STORAGE = FileStorage(workspace_root=_WORKSPACE_ROOT)
_APPROVALS = ApprovalHub()

# 约定：服务启动时加载 `.env`（若存在）；不覆盖进程外已注入的 env
_loaded_env_file = load_dotenv_for_workspace(workspace_root=_WORKSPACE_ROOT)

# 约定：后端启动时确保示例 skills 已安装到 generated root（开箱即用）
ensure_example_skills_installed(workspace_root=_WORKSPACE_ROOT)

app = FastAPI(title="skills-runtime-studio-mvp", version="0.0.0")
app.include_router(bind_create_skill_router(storage=_STORAGE))


class CreateSessionReq(BaseModel):
    title: Optional[str] = None
    filesystem_sources: Optional[List[str]] = Field(default=None, description="Filesystem source roots (one per entry).")


class SetSkillSourcesReq(BaseModel):
    filesystem_sources: List[str] = Field(default_factory=list)


class CreateRunReq(BaseModel):
    message: str = Field(min_length=1)

class DecideApprovalReq(BaseModel):
    decision: str = Field(min_length=1, description="approved|approved_for_session|denied|abort")


@app.get("/api/v1/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "workspace_root": str(_WORKSPACE_ROOT), "env_file": str(_loaded_env_file) if _loaded_env_file else None}


@app.get("/api/v1/sessions")
async def list_sessions() -> Dict[str, Any]:
    sessions = _STORAGE.list_sessions()
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "title": s.title,
                "updated_at": s.updated_at,
                "runs_count": s.runs_count,
            }
            for s in sessions
        ]
    }


@app.post("/api/v1/sessions", status_code=201)
async def create_session(body: CreateSessionReq = Body(default_factory=CreateSessionReq)) -> Dict[str, Any]:
    s = _STORAGE.create_session(title=body.title, filesystem_sources=body.filesystem_sources)
    return {"session_id": s.session_id, "created_at": s.created_at}


@app.delete("/api/v1/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    ok = _STORAGE.delete_session(session_id)
    if not ok:
        raise http_error("not_found", "session not found", status_code=404, details={"session_id": session_id})
    return None


@app.put("/api/v1/sessions/{session_id}/skills/sources")
async def set_skills_sources(session_id: str, body: SetSkillSourcesReq) -> Dict[str, Any]:
    try:
        _ = _STORAGE.session_dir(session_id)
    except Exception:
        pass

    try:
        cfg = _STORAGE.get_skills_config(session_id)
    except FileNotFoundError:
        raise http_error("not_found", "session not found", status_code=404, details={"session_id": session_id})

    sources = [str(p).strip() for p in (body.filesystem_sources or []) if str(p).strip()]
    cfg["filesystem_sources"] = sources
    cfg.setdefault("disabled_paths", [])
    _STORAGE.update_skills_config(session_id, cfg)
    return {"ok": True, "filesystem_sources": sources}


@app.get("/api/v1/sessions/{session_id}/skills")
async def list_skills(session_id: str) -> Dict[str, Any]:
    try:
        cfg = _STORAGE.get_skills_config(session_id)
    except FileNotFoundError:
        raise http_error("not_found", "session not found", status_code=404, details={"session_id": session_id})

    sources = cfg.get("filesystem_sources") or []
    sources = [str(r).strip() for r in sources if str(r).strip()]
    disabled_paths = cfg.get("disabled_paths") or []
    disabled_paths = [str(p).strip() for p in disabled_paths if str(p).strip()]

    disabled_set: set[Path] = set()
    for raw in disabled_paths:
        try:
            p = Path(str(raw)).expanduser()
            if not p.is_absolute():
                p = (_WORKSPACE_ROOT / p).resolve()
            else:
                p = p.resolve()
            disabled_set.add(p)
        except OSError:
            continue

    skills_cfg = skills_config_from_filesystem_sources(filesystem_sources=sources)
    mgr = SkillsManager(workspace_root=_WORKSPACE_ROOT, skills_config=skills_cfg)
    _ = mgr.scan()
    skills = []
    for s in mgr.list_skills(enabled_only=False):
        p = str(s.path or s.locator)
        enabled = True
        if s.path is not None:
            try:
                enabled = s.path.resolve() not in disabled_set
            except OSError:
                enabled = True
        skills.append(
            {
                "name": s.skill_name,
                "description": s.description,
                "path": p,
                "enabled": enabled,
                "dependencies": {"env_vars": list(s.required_env_vars or [])},
            }
        )

    # 补充字段：disabled_paths 与 scan errors/warnings 由上层决定是否展示
    return {
        "filesystem_sources": sources,
        "disabled_paths": disabled_paths,
        "skills": skills,
    }


def _build_agent(*, session_id: str, run_id: str) -> Agent:
    """
    构造 Agent（用于 runs 执行）。

    参数：
    - session_id：用于拼接 skills overlay 路径
    - run_id：用于 approvals（对每个 run 隔离审批）
    """

    overlay_paths = agent_bootstrap.discover_overlay_paths(workspace_root=_WORKSPACE_ROOT)
    skills_overlay = _STORAGE.skills_overlay_path(session_id)
    config_paths = list(overlay_paths) + [skills_overlay]

    import os

    backend_kind = str(os.getenv("STUDIO_LLM_BACKEND") or "openai").strip().lower()
    backend = None
    if backend_kind == "fake":
        # 离线回归夹具：用确定性的 tool_calls → tool → text 序列，覆盖 Run+Approvals 核心闭环。
        args = {"path": "studio_fake_llm_output.txt", "content": "hello from fake llm"}
        raw_args = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
        tc = ToolCall(call_id="call_1", name="file_write", args=args, raw_arguments=raw_args)
        backend = FakeChatBackend(
            calls=[
                FakeChatCall(
                    events=[
                        ChatStreamEvent(type="tool_calls", tool_calls=[tc], finish_reason="tool_calls"),
                        ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                    ]
                ),
                FakeChatCall(
                    events=[
                        ChatStreamEvent(type="text_delta", text="done"),
                        ChatStreamEvent(type="completed", finish_reason="stop"),
                    ]
                ),
            ]
        )

    return agent_bootstrap.build_agent(
        workspace_root=_WORKSPACE_ROOT,
        config_paths=config_paths,
        session_settings={},
        backend=backend,
        approval_provider=_APPROVALS.provider_for_run(run_id=run_id),
    )


@app.post("/api/v1/sessions/{session_id}/runs", status_code=201)
async def create_run(session_id: str, body: CreateRunReq) -> Dict[str, Any]:
    try:
        _ = _STORAGE.get_skills_config(session_id)
    except FileNotFoundError:
        raise http_error("not_found", "session not found", status_code=404, details={"session_id": session_id})

    run_id = f"run_{uuid.uuid4().hex}"
    _STORAGE.write_run_record(run_id=run_id, session_id=session_id)

    # 可靠性：提前创建 events.jsonl，避免 SSE 因文件缺失而“空转等待”。
    run_dir = _STORAGE.run_dir(run_id)
    events_jsonl_path = (run_dir / "events.jsonl").resolve()
    events_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    events_jsonl_path.touch(exist_ok=True)

    def _append_run_failed(*, error_kind: str, message: str, details: dict | None = None) -> None:
        """
        将 `run_failed` 事件追加到 events.jsonl（best-effort）。

        约定：
        - SSE `event` 取自 JSON 的 `type` 字段（参见 `studio_api.sse.stream_jsonl_as_sse`）
        - payload 至少包含 `error_kind/message/wal_locator`，以便前端展示与排障
        """

        obj = {
            "type": "run_failed",
            "timestamp": now_rfc3339(),
            "run_id": run_id,
            "payload": {
                "error_kind": str(error_kind or "unknown"),
                "message": str(message or ""),
                "retryable": False,
                "wal_locator": str(events_jsonl_path),
                **({"details": dict(details)} if isinstance(details, dict) else {}),
            },
        }
        try:
            with events_jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except Exception:
            # best-effort：落盘失败时不再抛出，避免线程异常导致更难排查
            logging.exception("append run_failed event failed (run_id=%s path=%s)", run_id, events_jsonl_path)

    def _worker() -> None:
        try:
            agent = _build_agent(session_id=session_id, run_id=run_id)
            for _ev in agent.run_stream(body.message, run_id=run_id):
                # Agent 已负责 events.jsonl 落盘；这里只需消费到结束，避免线程提前退出
                pass
        except Exception as e:
            _append_run_failed(error_kind="unknown", message=str(e), details={"exception_class": e.__class__.__name__})

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    return {"run_id": run_id}


@app.post("/api/v1/runs/{run_id}/approvals/{approval_key}")
async def decide_approval(run_id: str, approval_key: str, body: DecideApprovalReq) -> Dict[str, Any]:
    """
    提交 approval decision（用于解除 ApprovalProvider 阻塞）。

    说明：
    - 前端从 SSE 事件 `approval_requested` 拿到 approval_key；
    - 调用本接口提交 decision 后，后端会 resolve 对应 future，使 run 继续执行。
    """

    try:
        ok = _APPROVALS.decide(run_id=run_id, approval_key=approval_key, decision=body.decision)
    except ValueError as e:
        raise http_error("validation", str(e), status_code=400)

    if not ok:
        raise http_error("not_found", "approval not found", status_code=404, details={"run_id": run_id, "approval_key": approval_key})
    return {"ok": True}


@app.get("/api/v1/runs/{run_id}/approvals/pending")
async def list_pending_approvals(run_id: str) -> Dict[str, Any]:
    """列出 pending approvals（用于刷新/断线恢复）。"""

    return {"run_id": str(run_id), "approvals": _APPROVALS.list_pending(run_id=run_id)}


@app.get("/api/v1/runs/{run_id}/events/stream")
async def stream_run_events(run_id: str, request: Request) -> StreamingResponse:
    events_jsonl_path = (_WORKSPACE_ROOT / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()
    body = stream_jsonl_as_sse(request=request, jsonl_path=events_jsonl_path)
    return StreamingResponse(body, media_type="text/event-stream")
