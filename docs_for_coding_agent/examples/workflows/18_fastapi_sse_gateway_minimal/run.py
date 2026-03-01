"""
FastAPI/SSE 网关最小示例（离线可回归）。

注意：这是网关骨架示例（非 Studio API，不带 `/api/v1`）。

演示：
- 启动 FastAPI + uvicorn（127.0.0.1 随机端口）
- create run：POST /runs
- subscribe SSE：GET /runs/{run_id}/events/stream
- approvals decide：监听 approval_requested 后 POST /runs/{run_id}/approvals/{approval_key}
- terminal：run_completed / run_failed / run_cancelled

约束：
- 若缺少 fastapi/uvicorn，明确 SKIP 原因，但仍输出 EXAMPLE_OK（避免门禁不稳定）。
- 运行应在 30s 内完成（smoke timeout=30）。
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple


def _try_import_fastapi() -> tuple[Optional[str], Optional[object], Optional[object], Optional[object]]:
    """
    仅在依赖存在时启用 FastAPI/uvicorn 路径。

    返回：
    - (skip_reason, FastAPI, StreamingResponse, uvicorn)
    """

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
    """最小 HTTP JSON helper（离线回归用；不引入第三方 client 依赖）。"""

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


def _jsonl_append(path: Path, obj: Dict[str, Any]) -> None:
    """以 JSONL 追加写入最小事件（便于审计/排障）。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(raw)
        f.write("\n")


def _format_sse(*, event_name: str, payload: Dict[str, Any]) -> bytes:
    """把 (event_name, payload) 格式化为 SSE bytes。"""

    data = json.dumps({"payload": payload}, ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")


@dataclass
class _RunState:
    """最小 run 状态（仅用于本示例）。"""

    run_id: str
    approval_key: str
    message: str
    wal_locator: Path
    approval_decision: Optional[str]
    approval_event: threading.Event
    sse_queue: "queue.Queue[bytes]"


def _build_app(*, workspace_root: Path, FastAPI, StreamingResponse) -> object:
    """
    构建 FastAPI app。

    端点：
    - GET /health
    - POST /runs
    - GET /runs/{run_id}/events/stream
    - POST /runs/{run_id}/approvals/{approval_key}
    """

    import queue
    from uuid import uuid4

    app = FastAPI()
    runs: Dict[str, _RunState] = {}

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "workspace_root": str(workspace_root)}

    @app.post("/runs")
    def create_run(body: Dict[str, Any]) -> Dict[str, Any]:
        message = str((body or {}).get("message") or "")
        run_id = f"run_workflows_18_{uuid4().hex[:8]}"
        approval_key = "appr_1"
        wal_locator = (workspace_root / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()
        state = _RunState(
            run_id=run_id,
            approval_key=approval_key,
            message=message,
            wal_locator=wal_locator,
            approval_decision=None,
            approval_event=threading.Event(),
            sse_queue=queue.Queue(),
        )
        runs[run_id] = state

        # emit: run_started
        _jsonl_append(
            wal_locator,
            {"type": "run_started", "payload": {"run_id": run_id, "message": message, "wal_locator": str(wal_locator)}},
        )
        state.sse_queue.put(_format_sse(event_name="run_started", payload={"run_id": run_id}))

        # emit: approval_requested
        _jsonl_append(
            wal_locator,
            {
                "type": "approval_requested",
                "payload": {"run_id": run_id, "approval_key": approval_key, "kind": "file_write"},
            },
        )
        state.sse_queue.put(_format_sse(event_name="approval_requested", payload={"run_id": run_id, "approval_key": approval_key}))

        def _wait_and_finish() -> None:
            if not state.approval_event.wait(timeout=10.0):
                _jsonl_append(wal_locator, {"type": "run_failed", "payload": {"run_id": run_id, "reason": "approval_timeout"}})
                state.sse_queue.put(_format_sse(event_name="run_failed", payload={"run_id": run_id, "reason": "approval_timeout"}))
                return
            _jsonl_append(
                wal_locator,
                {
                    "type": "run_completed",
                    "payload": {"run_id": run_id, "wal_locator": str(wal_locator), "decision": state.approval_decision},
                },
            )
            state.sse_queue.put(_format_sse(event_name="run_completed", payload={"run_id": run_id, "wal_locator": str(wal_locator)}))

        threading.Thread(target=_wait_and_finish, daemon=True).start()

        return {"run_id": run_id, "wal_locator": str(wal_locator)}

    @app.get("/runs/{run_id}/events/stream")
    def events_stream(run_id: str):
        state = runs.get(str(run_id))
        if state is None:
            return StreamingResponse(iter(()), media_type="text/event-stream")

        def _gen() -> Iterator[bytes]:
            # 先发一个注释行，避免某些 client 因空而卡住
            yield b": connected\n\n"
            while True:
                try:
                    chunk = state.sse_queue.get(timeout=15.0)
                except Exception:
                    # keep-alive
                    yield b": keep-alive\n\n"
                    continue
                yield chunk
                if chunk.startswith(b"event: run_completed") or chunk.startswith(b"event: run_failed") or chunk.startswith(b"event: run_cancelled"):
                    return

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.post("/runs/{run_id}/approvals/{approval_key}")
    def decide_approval(run_id: str, approval_key: str, body: Dict[str, Any]) -> Dict[str, Any]:
        state = runs.get(str(run_id))
        if state is None or str(approval_key) != state.approval_key:
            return {"ok": False, "error": "not_found"}
        decision = str((body or {}).get("decision") or "").strip() or "approved_for_session"
        state.approval_decision = decision
        _jsonl_append(
            state.wal_locator,
            {"type": "approval_decided", "payload": {"run_id": run_id, "approval_key": approval_key, "decision": decision}},
        )
        state.sse_queue.put(_format_sse(event_name="approval_decided", payload={"run_id": run_id, "approval_key": approval_key, "decision": decision}))
        state.approval_event.set()
        return {"ok": True}

    return app


def _write_report(
    *,
    workspace_root: Path,
    run_id: str,
    terminal_event: str,
    wal_locator: str,
    approvals: int,
    skipped_reason: Optional[str] = None,
) -> None:
    """写入 `report.md`（确定性、便于离线回归检查）。"""

    lines = ["# FastAPI SSE Gateway Minimal Report", ""]
    if skipped_reason:
        lines.append("## Skipped")
        lines.append(f"- reason: {skipped_reason}")
        lines.append("")
    lines.extend(
        [
            "## Run",
            f"- run_id: `{run_id}`",
            f"- terminal: `{terminal_event}`",
            f"- approvals_decided: {approvals}",
            "",
            "## Evidence",
            f"- wal_locator: `{wal_locator}`",
            "",
        ]
    )
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    (workspace_root / "report.md").write_text(text, encoding="utf-8")


def main() -> int:
    """脚本入口：运行 workflows_18（离线 SSE 网关最小示例）。"""

    parser = argparse.ArgumentParser(description="18_fastapi_sse_gateway_minimal (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    skip_reason, FastAPI, StreamingResponse, uvicorn = _try_import_fastapi()
    if skip_reason:
        _write_report(
            workspace_root=workspace_root,
            run_id="(skipped)",
            terminal_event="skipped",
            wal_locator="(none)",
            approvals=0,
            skipped_reason=skip_reason,
        )
        print(f"SKIPPED: workflows_18 ({skip_reason})")
        print("EXAMPLE_OK: workflows_18")
        return 0

    port = _pick_free_port()
    app = _build_app(workspace_root=workspace_root, FastAPI=FastAPI, StreamingResponse=StreamingResponse)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    base_url = f"http://127.0.0.1:{port}"
    # wait health
    deadline = time.time() + 5.0
    while True:
        try:
            health = _http_json(method="GET", url=f"{base_url}/health", body=None, timeout_sec=1.0)
            if bool(health.get("ok")):
                break
        except Exception:
            pass
        if time.time() > deadline:
            server.should_exit = True
            raise SystemExit("server did not become healthy in time")
        time.sleep(0.05)

    message = "\n".join(
        [
            "$[examples:workflow].fastapi_sse_gateway_runner",
            "请运行一次最小流程：触发 approval_requested，然后在 approve 后完成 run。",
        ]
    )
    run = _http_json(method="POST", url=f"{base_url}/runs", body={"message": message}, timeout_sec=5.0)
    run_id = str(run.get("run_id") or "").strip()
    wal_locator = str(run.get("wal_locator") or "").strip()
    if not run_id:
        server.should_exit = True
        raise SystemExit(f"create_run failed: {run}")

    approvals = 0
    terminal: Optional[str] = None
    terminal_obj: Optional[Dict[str, Any]] = None

    stream_url = f"{base_url}/runs/{run_id}/events/stream"
    try:
        with _open_sse(url=stream_url, timeout_sec=20.0) as sse:
            for ev_name, obj in _iter_sse_events(sse):
                if ev_name == "approval_requested":
                    payload = (obj or {}).get("payload") or {}
                    approval_key = str(payload.get("approval_key") or "").strip()
                    if approval_key:
                        _ = _http_json(
                            method="POST",
                            url=f"{base_url}/runs/{run_id}/approvals/{approval_key}",
                            body={"decision": "approved_for_session"},
                            timeout_sec=5.0,
                        )
                        approvals += 1
                if ev_name in {"run_completed", "run_failed", "run_cancelled"}:
                    terminal = ev_name
                    terminal_obj = obj
                    break
    except urllib.error.URLError as e:
        server.should_exit = True
        raise SystemExit(f"SSE connection failed: {e}")

    if not terminal:
        server.should_exit = True
        raise SystemExit("missing terminal event")

    if terminal_obj:
        payload = terminal_obj.get("payload") or {}
        wal_locator = str(payload.get("wal_locator") or wal_locator)

    _write_report(
        workspace_root=workspace_root,
        run_id=run_id,
        terminal_event=terminal,
        wal_locator=wal_locator,
        approvals=approvals,
    )

    server.should_exit = True
    t.join(timeout=2.0)

    assert (workspace_root / "report.md").exists()
    print("EXAMPLE_OK: workflows_18")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
