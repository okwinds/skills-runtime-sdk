from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from agent_sdk.cli.main import main


def _parse_last_json(stdout: str) -> Dict[str, Any]:
    """解析 stdout 最后一行 JSON。"""

    text = (stdout or "").strip().splitlines()[-1]
    obj = json.loads(text)
    assert isinstance(obj, dict)
    return obj


def test_cli_runs_metrics_by_run_id_ok(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    run_id = "r1"
    events_jsonl_path = tmp_path / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl"
    events_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    events_jsonl_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": run_id, "payload": {}}),
                json.dumps({"type": "run_completed", "timestamp": "2026-02-09T00:00:01Z", "run_id": run_id, "payload": {}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    code = main(["runs", "metrics", "--workspace-root", str(tmp_path), "--run-id", run_id])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload["run_id"] == run_id
    assert payload["status"] == "completed"


def test_cli_runs_metrics_wal_locator_ok(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    events_jsonl_path = tmp_path / "events.jsonl"
    events_jsonl_path.write_text(
        json.dumps({"type": "run_started", "timestamp": "2026-02-09T00:00:00Z", "run_id": "r1", "payload": {}})
        + "\n",
        encoding="utf-8",
    )
    code = main(["runs", "metrics", "--workspace-root", str(tmp_path), "--wal-locator", str(events_jsonl_path)])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload["run_id"] == "r1"


def test_cli_runs_metrics_not_found_exit_22(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["runs", "metrics", "--workspace-root", str(tmp_path), "--run-id", "missing"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 22
    assert payload["status"] == "unknown"
    assert payload["errors"][0]["kind"] in {"not_found", "invalid_wal"}


def test_cli_runs_metrics_workspace_root_invalid_validation(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    missing = tmp_path / "nope"
    code = main(["runs", "metrics", "--workspace-root", str(missing), "--run-id", "r1"])
    out = capsys.readouterr().out
    assert code in {2, 20}
    # argparse/CLI 兜底可能返回 2；本断言仅确保不会抛异常
    if out.strip():
        _ = _parse_last_json(out)


def test_cli_runs_metrics_non_filesystem_wal_locator_exit_23(capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["runs", "metrics", "--workspace-root", ".", "--wal-locator", "wal://in-memory/test-run#run_id=r1"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 23
    assert payload["status"] == "unknown"
    assert any(e.get("kind") == "not_supported" for e in (payload.get("errors") or []))
