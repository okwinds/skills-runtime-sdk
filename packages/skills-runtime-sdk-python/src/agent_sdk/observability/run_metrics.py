"""
Run metrics summary（离线重算，可复刻）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/observability.md`
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _parse_rfc3339_to_dt(ts: str) -> datetime:
    """
    解析 RFC3339 时间字符串为 datetime（UTC）。

    参数：
    - ts：RFC3339 时间字符串（例如 `2026-02-09T00:00:00Z`）

    返回：
    - datetime（timezone-aware，UTC）

    异常：
    - ValueError：解析失败
    """

    raw = (ts or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _new_summary() -> Dict[str, Any]:
    """返回一个空的 RunMetricsSummary（字段稳定）。"""

    return {
        "run_id": "",
        "status": "unknown",
        "started_at": None,
        "ended_at": None,
        "wall_time_ms": 0,
        "counts": {
            "turns_total": 0,
            "llm_requests_total": 0,
            "tool_calls_total": 0,
            "approvals_requested_total": 0,
            "approvals_decided_total": 0,
            "human_requests_total": 0,
        },
        "tools": {"by_name": {}, "duration_ms_total": 0},
        "errors": [],
    }


def compute_run_metrics_summary(*, wal_locator: str) -> Dict[str, Any]:
    """
    从 events.jsonl 计算 RunMetricsSummary（离线可重算）。

    参数：
    - wal_locator：WAL 定位符；仅支持本地文件路径（当为 `wal://...` 等非文件 locator 时返回 not_supported）

    返回：
    - dict：RunMetricsSummary（JSONable）
    """

    summary = _new_summary()
    loc = str(wal_locator or "").strip()
    if not loc:
        summary["errors"].append({"kind": "validation", "message": "wal_locator is empty"})
        return summary

    # 非文件 locator：明确 not_supported（不得静默产出错误统计）
    if "://" in loc:
        summary["errors"].append({"kind": "not_supported", "message": f"metrics only supports filesystem wal_locator, got: {loc}"})
        return summary

    events_path = Path(loc)
    if not events_path.exists():
        summary["errors"].append({"kind": "not_found", "message": f"events file not found: {events_path}"})
        return summary

    run_id: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    has_completed = False
    has_failed = False
    has_cancelled = False
    run_failed_payload: Optional[Dict[str, Any]] = None
    turn_ids: set[str] = set()

    def _add_invalid_wal(message: str) -> None:
        """追加 invalid_wal 错误并将 status 固定为 unknown。"""

        summary["errors"].append({"kind": "invalid_wal", "message": message})
        summary["status"] = "unknown"

    try:
        text = Path(events_path).read_text(encoding="utf-8")
    except Exception as exc:
        _add_invalid_wal(f"failed to read events file: {exc}")
        return summary

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception as exc:
            _add_invalid_wal(f"invalid json line: {exc}")
            return summary

        if not isinstance(ev, dict):
            _add_invalid_wal("event must be an object")
            return summary

        rid = ev.get("run_id")
        if run_id is None:
            run_id = str(rid or "")
            summary["run_id"] = run_id
        else:
            if str(rid or "") != run_id:
                _add_invalid_wal("inconsistent run_id detected in WAL")
                return summary

        turn_id = ev.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            turn_ids.add(turn_id)

        typ = str(ev.get("type") or "")
        ts = ev.get("timestamp") or ev.get("ts") or None
        ts_str = str(ts) if isinstance(ts, str) else None

        if typ == "run_started" and started_at is None and ts_str:
            started_at = ts_str
        if typ in {"run_completed", "run_failed", "run_cancelled"} and ts_str:
            ended_at = ts_str

        if typ == "run_completed":
            has_completed = True
        elif typ == "run_failed":
            has_failed = True
            payload = ev.get("payload")
            if isinstance(payload, dict):
                run_failed_payload = payload
        elif typ == "run_cancelled":
            has_cancelled = True

        # counts
        if typ == "llm_request_started":
            summary["counts"]["llm_requests_total"] += 1
        elif typ == "approval_requested":
            summary["counts"]["approvals_requested_total"] += 1
        elif typ == "approval_decided":
            summary["counts"]["approvals_decided_total"] += 1
        elif typ == "human_request":
            summary["counts"]["human_requests_total"] += 1
        elif typ == "tool_call_finished":
            summary["counts"]["tool_calls_total"] += 1
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            tool = str(payload.get("tool") or "")
            result = payload.get("result") or {}
            if not isinstance(result, dict):
                result = {}
            ok = bool(result.get("ok") is True)
            duration_ms = int(result.get("duration_ms") or 0)
            summary["tools"]["duration_ms_total"] += max(duration_ms, 0)
            by_name: Dict[str, Any] = summary["tools"]["by_name"]
            if tool not in by_name:
                by_name[tool] = {"calls": 0, "ok": 0, "failed": 0, "duration_ms_total": 0}
            bucket = by_name[tool]
            bucket["calls"] += 1
            bucket["duration_ms_total"] += max(duration_ms, 0)
            if ok:
                bucket["ok"] += 1
            else:
                bucket["failed"] += 1

    summary["counts"]["turns_total"] = len(turn_ids)
    summary["started_at"] = started_at
    summary["ended_at"] = ended_at

    if has_completed:
        summary["status"] = "completed"
    elif has_failed:
        summary["status"] = "failed"
    elif has_cancelled:
        summary["status"] = "cancelled"
    else:
        summary["status"] = "unknown"

    if has_failed and run_failed_payload is not None:
        kind = str(run_failed_payload.get("error_kind") or "")
        msg = str(run_failed_payload.get("message") or "")
        if kind or msg:
            summary["errors"].append({"kind": kind or "unknown", "message": msg})

    if started_at and ended_at:
        try:
            dt0 = _parse_rfc3339_to_dt(started_at)
            dt1 = _parse_rfc3339_to_dt(ended_at)
            summary["wall_time_ms"] = int((dt1 - dt0).total_seconds() * 1000)
        except Exception as exc:
            _add_invalid_wal(f"failed to parse timestamps: {exc}")

    return summary
