from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.state import jsonl_wal as jsonl_wal_module
from skills_runtime.state.fork import fork_run_events_jsonl
from skills_runtime.state.jsonl_wal import JsonlWal


def _event(idx: int, *, run_id: str = "run_1") -> AgentEvent:
    """构造稳定的 WAL 测试事件。"""

    return AgentEvent(
        type="test",
        timestamp=f"2026-06-20T00:00:{idx:02d}Z",
        run_id=run_id,
        payload={"idx": idx},
    )


def _line(event: AgentEvent) -> str:
    """把测试事件编码成现有 WAL 行格式（不含换行）。"""

    payload = event.model_dump(by_alias=True, exclude_none=True)
    return json.dumps(payload, ensure_ascii=False)


def _write_lines(path: Path, events: list[AgentEvent]) -> bytes:
    """写入完整 JSONL 事件并返回写入的原始字节。"""

    data = "".join(f"{_line(event)}\n" for event in events).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def test_reopen_truncates_invalid_tail_half_line_and_keeps_complete_events(tmp_path: Path) -> None:
    """重开 WAL 时只截断尾部无换行半行，保留之前完整事件。"""

    wal_path = tmp_path / "events.jsonl"
    event = _event(1)
    expected = _write_lines(wal_path, [event])
    wal_path.write_bytes(expected + b'{"type":"partial"')

    wal = JsonlWal(wal_path)

    assert wal_path.read_bytes() == expected
    assert list(wal.iter_events()) == [event]
    assert wal.append(_event(2)) == 1


def test_reopen_truncates_valid_json_tail_without_newline(tmp_path: Path) -> None:
    """即便尾部半行本身是合法 JSON，只要缺少换行也必须截断。"""

    wal_path = tmp_path / "events.jsonl"
    event = _event(1)
    expected = _write_lines(wal_path, [event])
    half_line = _line(_event(2)).encode("utf-8")
    wal_path.write_bytes(expected + half_line)

    wal = JsonlWal(wal_path)

    assert wal_path.read_bytes() == expected
    assert list(wal.iter_events()) == [event]


def test_iter_events_raises_for_corrupt_middle_line(tmp_path: Path) -> None:
    """带换行的中间坏行表示持久化损坏，必须 fail-loud。"""

    wal_path = tmp_path / "events.jsonl"
    first = _event(1)
    second = _event(2)
    wal_path.write_text(f"{_line(first)}\nnot-json\n{_line(second)}\n", encoding="utf-8")
    wal = JsonlWal(wal_path)
    wal_corrupt_error = getattr(jsonl_wal_module, "WalCorruptError")

    with pytest.raises(wal_corrupt_error) as exc_info:
        list(wal.iter_events())

    message = str(exc_info.value)
    assert "line=2" in message
    assert "not-json" in message


def test_reopen_keeps_normal_newline_terminated_wal_unchanged(tmp_path: Path) -> None:
    """以换行结束的正常 WAL 重开后不能被截断或误删。"""

    wal_path = tmp_path / "events.jsonl"
    events = [_event(1), _event(2)]
    expected = _write_lines(wal_path, events)

    wal = JsonlWal(wal_path)

    assert wal_path.read_bytes() == expected
    assert list(wal.iter_events()) == events


def test_append_iter_events_index_and_fork_offsets_remain_monotonic(tmp_path: Path) -> None:
    """append index、迭代顺序与 fork 前缀偏移保持既有语义。"""

    src_wal_path = tmp_path / "src" / "events.jsonl"
    dst_wal_path = tmp_path / "dst" / "events.jsonl"
    wal = JsonlWal(src_wal_path)
    indices = [wal.append(_event(idx, run_id="src_run")) for idx in range(3)]

    assert indices == [0, 1, 2]
    assert [event.payload["idx"] for event in wal.iter_events()] == [0, 1, 2]

    fork_run_events_jsonl(
        src_wal_path=src_wal_path,
        dst_wal_path=dst_wal_path,
        new_run_id="dst_run",
        up_to_index_inclusive=1,
    )
    forked_wal = JsonlWal(dst_wal_path)

    assert [event.payload["idx"] for event in forked_wal.iter_events()] == [0, 1]
    assert [event.run_id for event in forked_wal.iter_events()] == ["dst_run", "dst_run"]
    assert JsonlWal(src_wal_path).append(_event(3, run_id="src_run")) == 3


def test_empty_newline_only_and_whole_file_half_line_edges(tmp_path: Path) -> None:
    """覆盖空文件、仅换行文件，以及整文件无换行的半行截断。"""

    empty_path = tmp_path / "empty.jsonl"
    empty_path.write_bytes(b"")
    assert list(JsonlWal(empty_path).iter_events()) == []
    assert empty_path.read_bytes() == b""

    newline_path = tmp_path / "newline.jsonl"
    newline_path.write_bytes(b"\n")
    assert list(JsonlWal(newline_path).iter_events()) == []
    assert newline_path.read_bytes() == b"\n"

    whole_half_line_path = tmp_path / "whole-half-line.jsonl"
    whole_half_line_path.write_text(_line(_event(1)), encoding="utf-8")
    assert list(JsonlWal(whole_half_line_path).iter_events()) == []
    assert whole_half_line_path.read_bytes() == b""


def test_new_wal_fsyncs_parent_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """新建 WAL 文件后对父目录做 best-effort fsync。"""

    calls: list[int] = []

    def fake_fsync(fd: int) -> None:
        calls.append(fd)

    monkeypatch.setattr(os, "fsync", fake_fsync)

    wal = JsonlWal(tmp_path / "run" / "events.jsonl")
    wal.close()

    assert len(calls) == 1


def test_directory_fsync_failure_warns_but_does_not_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """目录 fsync 不支持或失败时记录 warning，但 WAL 初始化不崩溃。"""

    def fail_fsync(fd: int) -> None:
        raise OSError("fsync unsupported")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    wal = JsonlWal(tmp_path / "run" / "events.jsonl")
    wal.close()

    assert "fsync" in caplog.text
    assert "events.jsonl" in caplog.text


def test_reopen_truncates_tail_half_line_larger_than_scan_block(tmp_path: Path) -> None:
    """尾部半行超过 64KB 时，块回扫仍能定位最后一个换行。"""

    wal_path = tmp_path / "events.jsonl"
    events = [_event(1), _event(2)]
    expected = _write_lines(wal_path, events)
    large_half_line = b"x" * (64 * 1024 + 123)
    wal_path.write_bytes(expected + large_half_line)

    wal = JsonlWal(wal_path)

    assert wal_path.read_bytes() == expected
    assert list(wal.iter_events()) == events


def test_repair_runs_under_process_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """repair 初始化路径必须进入进程级 flock 临界区。"""

    if jsonl_wal_module.fcntl is None:
        pytest.skip("fcntl is unavailable on this platform")

    calls: list[int] = []

    def fake_flock(fd: int, operation: int) -> None:
        calls.append(operation)

    wal_path = tmp_path / "events.jsonl"
    wal_path.write_bytes(_line(_event(1)).encode("utf-8"))
    monkeypatch.setattr(jsonl_wal_module.fcntl, "flock", fake_flock)

    JsonlWal(wal_path)

    assert calls == [jsonl_wal_module.fcntl.LOCK_EX, jsonl_wal_module.fcntl.LOCK_UN]
