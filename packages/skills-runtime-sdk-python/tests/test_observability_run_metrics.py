from __future__ import annotations

import json
from pathlib import Path

import pytest

from skills_runtime.observability.run_metrics import compute_run_metrics_summary


def _write_events(path: Path, events: list[dict]) -> None:
    """将 events 以 JSONL 写入到 path。"""

    lines = [json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in events]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def test_run_metrics_completed_with_tool_aggregation(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    _write_events(
        events_jsonl_path,
        [
            {"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "turn_id": "t1", "payload": {}},
            {"type": "llm_request_started", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r1", "turn_id": "t1", "payload": {}},
            {
                "type": "tool_call_finished",
                "timestamp": "2026-02-09T00:00:02Z",
                "run_id": "r1",
                "turn_id": "t1",
                "payload": {"tool": "list_dir", "result": {"ok": True, "duration_ms": 12}},
            },
            {
                "type": "tool_call_finished",
                "timestamp": "2026-02-09T00:00:03Z",
                "run_id": "r1",
                "turn_id": "t2",
                "payload": {"tool": "apply_patch", "result": {"ok": False, "duration_ms": 7}},
            },
            {"type": "run_completed", "timestamp": "2026-02-09T00:00:04Z", "run_id": "r1", "turn_id": None, "payload": {}},
        ],
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["run_id"] == "r1"
    assert m["status"] == "completed"
    assert m["counts"]["llm_requests_total"] == 1
    assert m["counts"]["tool_calls_total"] == 2
    assert m["tools"]["duration_ms_total"] == 19
    assert m["tools"]["by_name"]["list_dir"]["calls"] == 1
    assert m["tools"]["by_name"]["list_dir"]["ok"] == 1
    assert m["tools"]["by_name"]["apply_patch"]["failed"] == 1


def test_run_metrics_tool_aggregation_falls_back_to_name_field(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 3.2）：
    - consumer 必须优先读取 payload.tool；旧 WAL 可能只有 payload.name；
    - metrics 不得把 name-only 事件聚合到空字符串 bucket。
    """

    events_jsonl_path = tmp_path / "events.jsonl"
    _write_events(
        events_jsonl_path,
        [
            {"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "payload": {}},
            {"type": "tool_call_finished", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r1", "payload": {"name": "legacy_tool", "result": {"ok": True, "duration_ms": 3}}},
        ],
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["tools"]["by_name"]["legacy_tool"]["calls"] == 1
    assert "" not in m["tools"]["by_name"]


def test_run_metrics_failed_records_error(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    _write_events(
        events_jsonl_path,
        [
            {"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "payload": {}},
            {"type": "run_failed", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r1", "payload": {"error_kind": "budget_exceeded", "message": "x"}},
        ],
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["status"] == "failed"
    assert m["errors"][0]["kind"] == "budget_exceeded"


def test_run_metrics_cancelled(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    _write_events(
        events_jsonl_path,
        [
            {"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "payload": {}},
            {"type": "run_cancelled", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r1", "payload": {"message": "stop"}},
        ],
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["status"] == "cancelled"


def test_run_metrics_inconsistent_run_id_is_invalid(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    _write_events(
        events_jsonl_path,
        [
            {"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "payload": {}},
            {"type": "run_completed", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r2", "payload": {}},
        ],
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["status"] == "unknown"
    assert any(e["kind"] == "invalid_wal" for e in m["errors"])


def test_run_metrics_invalid_json_line(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    events_jsonl_path.write_text("{bad json}\n", encoding="utf-8")
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["status"] == "unknown"
    assert any(e["kind"] == "invalid_wal" for e in m["errors"])


def test_run_metrics_skips_invalid_json_lines_and_still_computes(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 7.2）：
    - metrics 遇到坏行必须“可观测但不中断”（skip + stable error counter），而不是直接崩溃/return。
    """

    events_jsonl_path = tmp_path / "events.jsonl"
    events_jsonl_path.write_text(
        "\n".join(
            [
                "{bad json}",
                json.dumps({"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "payload": {}}, ensure_ascii=False),
                json.dumps(
                    {"type": "tool_call_finished", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r1", "payload": {"tool": "list_dir", "result": {"ok": True, "duration_ms": 1}}},
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["run_id"] == "r1"
    assert m["counts"]["tool_calls_total"] == 1
    assert any(e["kind"] == "invalid_wal" for e in m["errors"])


def test_run_metrics_empty_file(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    events_jsonl_path.write_text("", encoding="utf-8")
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["status"] == "unknown"
    assert m["run_id"] == ""


def test_run_metrics_counts_turns_unique(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    _write_events(
        events_jsonl_path,
        [
            {"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "turn_id": "t1", "payload": {}},
            {"type": "tool_call_finished", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r1", "turn_id": "t1", "payload": {"tool": "list_dir", "result": {"ok": True, "duration_ms": 1}}},
            {"type": "tool_call_finished", "timestamp": "2026-02-09T00:00:02Z", "run_id": "r1", "turn_id": "t2", "payload": {"tool": "list_dir", "result": {"ok": True, "duration_ms": 1}}},
        ],
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["counts"]["turns_total"] == 2


def test_run_metrics_wall_time_ms(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    _write_events(
        events_jsonl_path,
        [
            {"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "payload": {}},
            {"type": "run_completed", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r1", "payload": {}},
        ],
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["wall_time_ms"] == 1000


def test_run_metrics_missing_duration_is_zero(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    _write_events(
        events_jsonl_path,
        [
            {"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "payload": {}},
            {"type": "tool_call_finished", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r1", "payload": {"tool": "list_dir", "result": {"ok": True}}},
        ],
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["tools"]["duration_ms_total"] == 0


def test_run_metrics_counts_approvals_and_human_requests(tmp_path: Path) -> None:
    events_jsonl_path = tmp_path / "events.jsonl"
    _write_events(
        events_jsonl_path,
        [
            {"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "payload": {}},
            {"type": "approval_requested", "timestamp": "2026-02-09T00:00:01Z", "run_id": "r1", "payload": {}},
            {"type": "approval_decided", "timestamp": "2026-02-09T00:00:02Z", "run_id": "r1", "payload": {}},
            {"type": "human_request", "timestamp": "2026-02-09T00:00:03Z", "run_id": "r1", "payload": {}},
        ],
    )
    m = compute_run_metrics_summary(wal_locator=str(events_jsonl_path))
    assert m["counts"]["approvals_requested_total"] == 1
    assert m["counts"]["approvals_decided_total"] == 1
    assert m["counts"]["human_requests_total"] == 1


def test_run_metrics_non_filesystem_locator_is_not_supported() -> None:
    m = compute_run_metrics_summary(wal_locator="wal://in-memory/test-run#run_id=r1")
    assert any(e.get("kind") == "not_supported" for e in (m.get("errors") or []))
