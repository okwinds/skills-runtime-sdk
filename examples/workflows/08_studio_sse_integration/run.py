"""
Studio API + SSE 端到端集成示例（需要显式 opt-in）。

该脚本会：
- 创建 session（skills roots 指向本目录的 skills/）
- 创建 run（message 含 $[web:mvp].studio_demo_writer）
- 订阅 SSE events stream，并自动批准 approvals

默认不在离线门禁中运行（需要 env 开关）。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple


@dataclass(frozen=True)
class StudioConfig:
    base_url: str
    integration_enabled: bool


def _load_config() -> StudioConfig:
    enabled = (os.getenv("SKILLS_RUNTIME_SDK_RUN_INTEGRATION") or "").strip() == "1"
    base_url = (os.getenv("SKILLS_RUNTIME_STUDIO_BASE_URL") or "http://127.0.0.1:8000").strip().rstrip("/")
    return StudioConfig(base_url=base_url, integration_enabled=enabled)


def _http_json(
    *,
    method: str,
    url: str,
    body: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 10.0,
) -> Dict[str, Any]:
    payload = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data) if data else {}


def _open_sse(*, url: str, timeout_sec: float = 60.0):
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"}, method="GET")
    return urllib.request.urlopen(req, timeout=timeout_sec)


def _iter_sse_events(stream) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """
    解析 SSE（event/data）。

    Studio backend 输出格式（单条消息）：
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


def main() -> int:
    cfg = _load_config()
    if not cfg.integration_enabled:
        print("SKIPPED: workflows_08 (set SKILLS_RUNTIME_SDK_RUN_INTEGRATION=1 to enable)")
        return 0

    skills_root = (Path(__file__).resolve().parent / "skills").resolve()
    if not skills_root.exists():
        raise SystemExit(f"skills root not found: {skills_root}")

    # 1) health check
    health = _http_json(method="GET", url=f"{cfg.base_url}/api/v1/health", body=None, timeout_sec=5.0)
    if not bool(health.get("ok")):
        raise SystemExit(f"Studio health check failed: {health}")
    print(f"Studio OK: workspace_root={health.get('workspace_root')}")

    # 2) create session
    sess = _http_json(
        method="POST",
        url=f"{cfg.base_url}/api/v1/sessions",
        body={"title": "workflows_08", "skills_roots": [str(skills_root)]},
        timeout_sec=10.0,
    )
    session_id = str(sess.get("session_id") or "").strip()
    if not session_id:
        raise SystemExit(f"create_session failed: {sess}")
    print(f"session_id={session_id}")

    # 3) create run
    message = "\n".join(
        [
            "$[web:mvp].studio_demo_writer",
            "请写入 studio_demo_output.txt，内容必须包含 STUDIO_DEMO_OK。",
        ]
    )
    run = _http_json(
        method="POST",
        url=f"{cfg.base_url}/api/v1/sessions/{session_id}/runs",
        body={"message": message},
        timeout_sec=10.0,
    )
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        raise SystemExit(f"create_run failed: {run}")
    print(f"run_id={run_id}")

    # 4) SSE stream + auto-approvals
    stream_url = f"{cfg.base_url}/api/v1/runs/{run_id}/events/stream"
    terminal: Optional[str] = None
    terminal_obj: Optional[Dict[str, Any]] = None

    try:
        with _open_sse(url=stream_url, timeout_sec=60.0) as sse:
            for ev_name, obj in _iter_sse_events(sse):
                print(f"SSE: {ev_name}")

                if ev_name == "approval_requested":
                    payload = (obj or {}).get("payload") or {}
                    approval_key = str(payload.get("approval_key") or "").strip()
                    if approval_key:
                        _ = _http_json(
                            method="POST",
                            url=f"{cfg.base_url}/api/v1/runs/{run_id}/approvals/{approval_key}",
                            body={"decision": "approved_for_session"},
                            timeout_sec=10.0,
                        )
                        print(f"approval decided: {approval_key}")

                if ev_name in {"run_completed", "run_failed", "run_cancelled"}:
                    terminal = ev_name
                    terminal_obj = obj
                    break
    except urllib.error.URLError as e:
        raise SystemExit(f"SSE connection failed: {e}")

    print(f"terminal={terminal}")
    if terminal_obj:
        payload = terminal_obj.get("payload") or {}
        print(f"events_path={payload.get('events_path')}")

    # best-effort cleanup：异步删除 session（避免本地堆积）
    try:
        _http_json(method="DELETE", url=f"{cfg.base_url}/api/v1/sessions/{session_id}", body=None, timeout_sec=10.0)
    except Exception:
        pass

    # 控制台给一点时间，让用户看清楚输出（可选）
    time.sleep(0.1)
    print("EXAMPLE_OK: workflows_08 (integration)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

