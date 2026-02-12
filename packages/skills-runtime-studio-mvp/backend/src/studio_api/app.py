from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent_sdk import Agent
from agent_sdk import bootstrap as agent_bootstrap
from agent_sdk.config.loader import AgentSdkLlmConfig, load_config_dicts
from agent_sdk.llm.openai_chat import OpenAIChatCompletionsBackend
from agent_sdk.skills.manager import SkillsManager

from studio_api.approvals import ApprovalHub
from studio_api.envfile import load_dotenv_for_workspace
from studio_api.errors import http_error
from studio_api.example_skills import ensure_example_skills_installed
from studio_api.sse import stream_jsonl_as_sse
from studio_api.skills_create import bind_create_skill_router
from studio_api.skills_overlay import skills_v2_config_from_roots
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
    skills_roots: Optional[List[str]] = Field(default=None)


class SetSkillRootsReq(BaseModel):
    roots: List[str] = Field(default_factory=list)


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
    s = _STORAGE.create_session(title=body.title, skills_roots=body.skills_roots)
    return {"session_id": s.session_id, "created_at": s.created_at}


@app.delete("/api/v1/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    ok = _STORAGE.delete_session(session_id)
    if not ok:
        raise http_error("not_found", "session not found", status_code=404, details={"session_id": session_id})
    return None


@app.put("/api/v1/sessions/{session_id}/skills/roots")
async def set_skills_roots(session_id: str, body: SetSkillRootsReq) -> Dict[str, Any]:
    try:
        _ = _STORAGE.session_dir(session_id)
    except Exception:
        pass

    try:
        cfg = _STORAGE.get_skills_config(session_id)
    except FileNotFoundError:
        raise http_error("not_found", "session not found", status_code=404, details={"session_id": session_id})

    roots = [str(p).strip() for p in (body.roots or []) if str(p).strip()]
    cfg["roots"] = roots
    cfg["explicit_empty"] = bool(len(roots) == 0)
    cfg.setdefault("disabled_paths", [])
    cfg.setdefault("mode", "explicit")
    _STORAGE.update_skills_config(session_id, cfg)
    return {"ok": True, "roots": roots}


@app.get("/api/v1/sessions/{session_id}/skills")
async def list_skills(session_id: str) -> Dict[str, Any]:
    try:
        cfg = _STORAGE.ensure_skills_roots_configured(session_id)
    except FileNotFoundError:
        raise http_error("not_found", "session not found", status_code=404, details={"session_id": session_id})

    roots = cfg.get("roots") or []
    roots = [str(r).strip() for r in roots if str(r).strip()]
    disabled_paths = cfg.get("disabled_paths") or []
    disabled_paths = [str(p).strip() for p in disabled_paths if str(p).strip()]

    skills_cfg = skills_v2_config_from_roots(roots=roots)
    mgr = SkillsManager(workspace_root=_WORKSPACE_ROOT, skills_config=skills_cfg)
    _ = mgr.scan()
    skills = []
    for s in mgr.list_skills(enabled_only=False):
        p = str(s.path or s.locator)
        enabled = True
        try:
            if s.path is not None and s.path in getattr(mgr, "_disabled_paths", set()):
                enabled = False
        except Exception:
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

    # 兼容字段：disabled_paths 与 scan errors/warnings 由上层决定是否展示
    return {
        "roots": roots,
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

    # 通过 bootstrap 得到有效 base_url/api_key_env（session_settings 先留空；后续可扩展）
    resolved = agent_bootstrap.resolve_effective_run_config(workspace_root=_WORKSPACE_ROOT, session_settings={})

    # 读取 overlay 合并后的默认 timeout/max_retries（best-effort）
    merged_dicts: List[Dict[str, Any]] = []
    from agent_sdk.config.defaults import load_default_config_dict
    import yaml

    merged_dicts.append(load_default_config_dict())
    for p in config_paths:
        obj = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
        if isinstance(obj, dict):
            merged_dicts.append(obj)
    merged_cfg = load_config_dicts(merged_dicts)

    llm_cfg = AgentSdkLlmConfig(
        base_url=str(resolved.base_url),
        api_key_env=str(resolved.api_key_env),
        timeout_sec=int(getattr(merged_cfg.llm, "timeout_sec", 60)),
        max_retries=int(getattr(merged_cfg.llm, "max_retries", 3)),
    )

    backend = OpenAIChatCompletionsBackend(llm_cfg)
    return Agent(
        workspace_root=_WORKSPACE_ROOT,
        backend=backend,
        config_paths=config_paths,
        approval_provider=_APPROVALS.provider_for_run(run_id=run_id),
    )


@app.post("/api/v1/sessions/{session_id}/runs", status_code=201)
async def create_run(session_id: str, body: CreateRunReq) -> Dict[str, Any]:
    try:
        _ = _STORAGE.ensure_skills_roots_configured(session_id)
    except FileNotFoundError:
        raise http_error("not_found", "session not found", status_code=404, details={"session_id": session_id})

    run_id = f"run_{uuid.uuid4().hex}"
    _STORAGE.write_run_record(run_id=run_id, session_id=session_id)

    # 可靠性：提前创建 events.jsonl，避免 SSE 因文件缺失而“空转等待”。
    run_dir = _STORAGE.run_dir(run_id)
    events_path = (run_dir / "events.jsonl").resolve()
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.touch(exist_ok=True)

    def _append_run_failed(*, error_kind: str, message: str) -> None:
        """
        将 `run_failed` 事件追加到 events.jsonl（best-effort）。

        约定：
        - SSE `event` 取自 JSON 的 `type` 字段（参见 `studio_api.sse.stream_jsonl_as_sse`）
        - payload 至少包含 `error_kind/message/events_path`，以便前端展示与排障
        """

        obj = {
            "type": "run_failed",
            "timestamp": now_rfc3339(),
            "run_id": run_id,
            "payload": {
                "error_kind": str(error_kind or "unknown"),
                "message": str(message or ""),
                "retryable": False,
                "events_path": str(events_path),
            },
        }
        try:
            with events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except Exception:
            # best-effort：落盘失败时不再抛出，避免线程异常导致更难排查
            pass

    def _worker() -> None:
        try:
            agent = _build_agent(session_id=session_id, run_id=run_id)
            for _ev in agent.run_stream(body.message, run_id=run_id):
                # Agent 已负责 events.jsonl 落盘；这里只需消费到结束，避免线程提前退出
                pass
        except Exception as e:
            _append_run_failed(error_kind=e.__class__.__name__, message=str(e))

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
    events_path = (_WORKSPACE_ROOT / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()
    body = stream_jsonl_as_sse(request=request, jsonl_path=events_path)
    return StreamingResponse(body, media_type="text/event-stream")
