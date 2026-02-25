"""
FastAPI + SSE 网关 Pro（面向人类的应用示例）：
- 离线可回归：--mode offline --demo（服务+客户端一键跑通）
- 真模型可跑：--mode real --serve（OpenAICompatible）
- Skills-First：run task 中包含 `$[examples:app].*` mentions，WAL 中可看到 `skill_injected`
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

# 说明：用户可能从任意 cwd 启动本脚本；为避免 `import examples.*` 依赖 cwd，显式注入 repo_root。
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from examples.apps._shared.app_support import (  # noqa: E402
    assert_event_exists,
    assert_skill_injected,
    assert_tool_ok,
    build_openai_compatible_backend,
    env_or_default,
    write_overlay_for_app,
)
from skills_runtime.agent import Agent  # noqa: E402
from skills_runtime.llm.chat_sse import ChatStreamEvent  # noqa: E402
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall  # noqa: E402
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest  # noqa: E402
from skills_runtime.tools.protocol import ToolCall  # noqa: E402


def _try_import_fastapi():
    """依赖可选：fastapi + uvicorn。缺失时返回 (skip_reason, None...)."""

    try:
        from fastapi import FastAPI  # type: ignore[import-not-found]
        from fastapi.responses import StreamingResponse  # type: ignore[import-not-found]
        import uvicorn  # type: ignore[import-not-found]
    except Exception as e:
        return (f"missing dependency fastapi/uvicorn: {e}", None, None, None)
    return (None, FastAPI, StreamingResponse, uvicorn)


def _pick_free_port() -> int:
    """选择一个可用的本地随机端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        _, port = s.getsockname()
        return int(port)


def _http_json(
    *,
    method: str,
    url: str,
    body: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 10.0,
) -> Dict[str, Any]:
    """最小 HTTP JSON helper（不引入第三方 client 依赖）。"""

    payload = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data) if data else {}


def _open_sse(*, url: str, timeout_sec: float = 60.0):
    """打开 SSE 流（urllib 版本）。"""

    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"}, method="GET")
    return urllib.request.urlopen(req, timeout=timeout_sec)


def _iter_sse_events(stream) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """
    解析 SSE（event/data）。

    约定（单条消息）：
    - event: <type>
    - data: <one-line json>
    - blank line
    """

    event_name: Optional[str] = None
    data_line: Optional[str] = None

    while True:
        raw = stream.readline()
        if not raw:
            return
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_line = line[len("data:") :].strip()
            continue
        if line.strip() == "":
            if event_name and data_line:
                try:
                    obj = json.loads(data_line)
                except Exception:
                    obj = {"raw": data_line}
                yield (event_name, obj)
            event_name = None
            data_line = None


def _format_sse(*, event_name: str, payload: Dict[str, Any]) -> bytes:
    """把 (event_name, payload) 格式化为 SSE bytes。"""

    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")


@dataclass
class _ApprovalSlot:
    request: ApprovalRequest
    decision: Optional[ApprovalDecision] = None
    event: threading.Event = field(default_factory=threading.Event)


@dataclass
class _RunRecord:
    run_id: str
    workspace_root: Path
    wal_locator: Optional[str] = None
    queue: "queue.Queue[bytes]" = field(default_factory=queue.Queue)
    approvals: Dict[str, _ApprovalSlot] = field(default_factory=dict)
    done: threading.Event = field(default_factory=threading.Event)


class HttpApprovalProvider(ApprovalProvider):
    """
    通过 HTTP API 供外部决策的 ApprovalProvider（用于网关服务化示例）。

    行为：
    - request_approval 时注册 approval_key，并阻塞等待外部决策；
    - API 调用后唤醒等待并返回 decision；
    - 若超时未决策，fail-closed（DENIED）。
    """

    def __init__(self, record: _RunRecord) -> None:
        self._record = record

    async def request_approval(self, *, request: ApprovalRequest, timeout_ms: Optional[int] = None) -> ApprovalDecision:  # type: ignore[override]
        approval_key = str(request.approval_key or "")
        if not approval_key:
            return ApprovalDecision.DENIED

        slot = _ApprovalSlot(request=request)
        self._record.approvals[approval_key] = slot

        # 等待外部决策（默认 60s）
        timeout_sec = float(timeout_ms or 60000) / 1000.0
        ok = slot.event.wait(timeout=timeout_sec)
        if not ok:
            return ApprovalDecision.DENIED
        return slot.decision or ApprovalDecision.DENIED


def _build_offline_backend(*, report_md: str) -> FakeChatBackend:
    """离线 Fake backend：update_plan → file_write(report) → 完成。"""

    plan_1 = {
        "explanation": "SSE 网关：生成报告并落盘",
        "plan": [
            {"step": "生成报告", "status": "in_progress"},
            {"step": "落盘产物", "status": "pending"},
        ],
    }
    plan_2 = {
        "explanation": "SSE 网关：完成",
        "plan": [
            {"step": "生成报告", "status": "completed"},
            {"step": "落盘产物", "status": "completed"},
        ],
    }

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_plan1", name="update_plan", args=plan_1),
                            ToolCall(call_id="tc_report", name="file_write", args={"path": "report.md", "content": report_md}),
                            ToolCall(call_id="tc_plan2", name="update_plan", args=plan_2),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="网关 run 已完成。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _serve_gateway(
    *,
    workspace_root: Path,
    mode: str,
    skills_root: Path,
    host: str,
    port: int,
):
    """
    启动 FastAPI 网关服务（阻塞）。

    端点：
    - GET /health
    - POST /runs {message}
    - GET /runs/{run_id}/events/stream (SSE)
    - POST /runs/{run_id}/approvals/{approval_key} {decision: approve|deny}
    """

    skip_reason, FastAPI, StreamingResponse, uvicorn = _try_import_fastapi()
    if skip_reason:
        raise RuntimeError(skip_reason)

    app = FastAPI()
    runs: Dict[str, _RunRecord] = {}
    lock = threading.Lock()

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "workspace_root": str(workspace_root), "mode": mode}

    def _start_run_thread(*, record: _RunRecord, message: str) -> None:
        """
        在后台线程里运行 Agent，并把关键事件推入 SSE 队列。
        """

        run_task = "\n".join(
            [
                "$[examples:app].sse_gateway_planner",
                "$[examples:app].sse_gateway_writer",
                "$[examples:app].sse_gateway_reporter",
                "你正在运行一个 SSE 网关示例。",
                "必须使用工具完成：update_plan → file_write(report.md)。",
                f"用户消息：{message}",
            ]
        )

        overlay = write_overlay_for_app(
            workspace_root=record.workspace_root,
            skills_root=skills_root,
            safety_mode="ask",
            max_steps=40 if mode == "offline" else 120,
            llm_base_url=(env_or_default("OPENAI_BASE_URL", "https://api.openai.com/v1") if mode == "real" else None),
            planner_model=(env_or_default("SRS_MODEL_PLANNER", "gpt-4o-mini") if mode == "real" else None),
            executor_model=(env_or_default("SRS_MODEL_EXECUTOR", "gpt-4o-mini") if mode == "real" else None),
        )

        report_md = "\n".join(
            [
                "# SSE Gateway Report\n",
                "## Summary\n",
                "- 这是一个通过 SSE 观察 run 过程的示例产物。\n",
                "",
            ]
        )
        if mode == "offline":
            backend = _build_offline_backend(report_md=report_md)
            agent = Agent(
                model="fake-model",
                backend=backend,
                workspace_root=record.workspace_root,
                config_paths=[overlay],
                approval_provider=HttpApprovalProvider(record),
            )
        else:
            backend = build_openai_compatible_backend(config_paths=[overlay])
            agent = Agent(
                backend=backend,
                workspace_root=record.workspace_root,
                config_paths=[overlay],
                approval_provider=HttpApprovalProvider(record),
            )

        for ev in agent.run_stream(run_task, run_id=record.run_id):
            payload = ev.payload or {}
            if ev.type == "run_completed":
                record.wal_locator = str(payload.get("wal_locator") or "")
            record.queue.put(_format_sse(event_name=str(ev.type), payload={"type": ev.type, "payload": payload}))
            if ev.type == "run_completed":
                record.done.set()
                break

    @app.post("/runs")
    def create_run(body: Dict[str, Any]) -> Dict[str, Any]:
        message = str((body or {}).get("message") or "").strip()
        if not message:
            message = "生成一份简短的运行报告并落盘。"

        run_id = f"run_app_fastapi_sse_gateway_{int(time.time())}"
        run_ws = (workspace_root / "runs" / run_id).resolve()
        run_ws.mkdir(parents=True, exist_ok=True)

        record = _RunRecord(run_id=run_id, workspace_root=run_ws)
        with lock:
            runs[run_id] = record

        threading.Thread(target=_start_run_thread, kwargs={"record": record, "message": message}, daemon=True).start()
        return {"run_id": run_id}

    @app.get("/runs/{run_id}/events/stream")
    def stream(run_id: str):
        record = runs.get(str(run_id))
        if record is None:
            return StreamingResponse(iter(()), media_type="text/event-stream")

        def _gen() -> Iterator[bytes]:
            yield b": connected\n\n"
            while True:
                if record.done.is_set() and record.queue.empty():
                    return
                try:
                    chunk = record.queue.get(timeout=1.0)
                except Exception:
                    yield b": keep-alive\n\n"
                    continue
                yield chunk

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.post("/runs/{run_id}/approvals/{approval_key}")
    def decide(run_id: str, approval_key: str, body: Dict[str, Any]) -> Dict[str, Any]:
        record = runs.get(str(run_id))
        if record is None:
            return {"ok": False, "error": "run not found"}
        slot = record.approvals.get(str(approval_key))
        if slot is None:
            return {"ok": False, "error": "approval_key not found"}

        raw = str((body or {}).get("decision") or "").strip().lower()
        decision = ApprovalDecision.APPROVED_FOR_SESSION if raw in {"approve", "approved", "y", "yes"} else ApprovalDecision.DENIED
        slot.decision = decision
        slot.event.set()
        return {"ok": True, "decision": str(decision)}

    config = uvicorn.Config(app, host=host, port=int(port), log_level="warning")
    server = uvicorn.Server(config=config)
    server.run()


def _wait_http_ok(url: str, *, timeout_sec: float = 8.0) -> None:
    """等待 /health 就绪。"""

    t0 = time.monotonic()
    while True:
        if time.monotonic() - t0 > timeout_sec:
            raise TimeoutError("gateway server not ready")
        try:
            _http_json(method="GET", url=url, timeout_sec=1.0)
            return
        except Exception:
            time.sleep(0.1)


def _demo(*, workspace_root: Path, mode: str, skills_root: Path) -> int:
    """一键 demo：启动服务 → 创建 run → 订阅 SSE → 自动审批 → 结束。"""

    skip_reason, _, _, _ = _try_import_fastapi()
    if skip_reason:
        print(f"[skip] {skip_reason}")
        print("EXAMPLE_OK: app_fastapi_sse_gateway_pro (skipped)")
        return 0

    host = "127.0.0.1"
    port = _pick_free_port()
    base = f"http://{host}:{port}"

    th = threading.Thread(
        target=_serve_gateway,
        kwargs={"workspace_root": workspace_root, "mode": mode, "skills_root": skills_root, "host": host, "port": port},
        daemon=True,
    )
    th.start()

    _wait_http_ok(f"{base}/health", timeout_sec=10.0)
    run_obj = _http_json(method="POST", url=f"{base}/runs", body={"message": "生成一份简短的运行报告并落盘"}, timeout_sec=10.0)
    run_id = str(run_obj.get("run_id") or "")

    # 订阅 SSE
    sse = _open_sse(url=f"{base}/runs/{run_id}/events/stream", timeout_sec=30.0)
    completed = False
    wal_locator = ""
    for event_name, obj in _iter_sse_events(sse):
        if event_name != "approval_requested":
            # 降噪：仅打印少量关键事件
            if event_name in {"run_started", "tool_call_started", "tool_call_finished", "run_completed"}:
                print(f"[sse] {event_name}")
        if event_name == "approval_requested":
            payload = obj.get("payload") or {}
            approval_key = str(payload.get("approval_key") or payload.get("request", {}).get("approval_key") or "")
            # agent 事件 payload 里 approval_key 在 approval_requested 事件中
            if not approval_key:
                approval_key = str((payload.get("approval_key") or ""))
            if approval_key:
                _http_json(
                    method="POST",
                    url=f"{base}/runs/{run_id}/approvals/{approval_key}",
                    body={"decision": "approve"},
                    timeout_sec=5.0,
                )
        if event_name == "run_completed":
            payload = obj.get("payload") or {}
            wal_locator = str(payload.get("wal_locator") or "")
            completed = True
            break

    if not completed:
        raise AssertionError("demo did not reach run_completed")

    # 最小“跑通感”护栏：产物存在 + skills-first 证据存在
    report_path = (workspace_root / "runs" / run_id / "report.md").resolve()
    assert report_path.exists(), f"missing report artifact: {report_path}"
    if wal_locator:
        assert_skill_injected(wal_locator=wal_locator, mention_text="$[examples:app].sse_gateway_planner")
        assert_skill_injected(wal_locator=wal_locator, mention_text="$[examples:app].sse_gateway_writer")
        assert_skill_injected(wal_locator=wal_locator, mention_text="$[examples:app].sse_gateway_reporter")
        assert_event_exists(wal_locator=wal_locator, event_type="approval_requested")
        assert_event_exists(wal_locator=wal_locator, event_type="approval_decided")
        assert_tool_ok(wal_locator=wal_locator, tool="update_plan")
        assert_tool_ok(wal_locator=wal_locator, tool="file_write")

    print("EXAMPLE_OK: app_fastapi_sse_gateway_pro")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="fastapi_sse_gateway_pro (offline/real)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline", help="Run mode")
    parser.add_argument("--serve", action="store_true", help="Start gateway server and block")
    parser.add_argument("--demo", action="store_true", help="Run demo (start server + client, then exit)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (serve mode)")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    if args.demo or (args.mode == "offline" and not args.serve):
        return _demo(workspace_root=workspace_root, mode=args.mode, skills_root=skills_root)

    skip_reason, _, _, _ = _try_import_fastapi()
    if skip_reason:
        print(f"[error] {skip_reason}")
        return 2

    _serve_gateway(
        workspace_root=workspace_root,
        mode=args.mode,
        skills_root=skills_root,
        host=str(args.host),
        port=int(args.port),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
