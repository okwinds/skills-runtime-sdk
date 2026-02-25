"""
WAL Fork 辅助（Phase 4）。

对齐：
- `docs/specs/skills-runtime-sdk/docs/state.md` §5（Fork/Resume 语义）

最小语义：
- 从某个 `events.jsonl` 的行号（0-based）截取前缀事件；
- 写入到新 run 目录下，并把 copied events 的 `run_id` 重写为新 run_id；
- 该 fork 结果可用于后续 resume（summary 或 replay）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def fork_run_events_jsonl(
    *,
    src_wal_path: Path,
    dst_wal_path: Path,
    new_run_id: str,
    up_to_index_inclusive: int,
) -> None:
    """
    从 src_wal_path 截取 events.jsonl 前缀并写入 dst_wal_path。

    参数：
    - src_wal_path：源 WAL 路径
    - dst_wal_path：目标 WAL 路径（会覆盖写入）
    - new_run_id：新 run_id（用于重写事件字段）
    - up_to_index_inclusive：包含的最大行号（0-based；>=0）
    """

    if up_to_index_inclusive < 0:
        raise ValueError("up_to_index_inclusive must be >= 0")
    if not str(new_run_id or "").strip():
        raise ValueError("new_run_id must be non-empty")

    src = Path(src_wal_path)
    dst = Path(dst_wal_path)
    if not src.exists():
        raise FileNotFoundError(str(src))

    dst.parent.mkdir(parents=True, exist_ok=True)

    max_line = int(up_to_index_inclusive)
    out_lines: list[str] = []
    with src.open("r", encoding="utf-8") as f:
        for idx, raw in enumerate(f):
            if idx > max_line:
                break
            line = raw.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                obj["run_id"] = str(new_run_id)
                payload = obj.get("payload")
                if isinstance(payload, dict) and isinstance(payload.get("wal_locator"), str):
                    # best-effort：把 wal_locator 指向新路径，避免 fork 后 UI/审计展示误导。
                    payload["wal_locator"] = str(dst)
                out_lines.append(json.dumps(obj, ensure_ascii=False))

    with dst.open("w", encoding="utf-8") as f2:
        for line2 in out_lines:
            f2.write(line2)
            f2.write("\n")


def fork_run(
    *,
    workspace_root: Path,
    src_run_id: str,
    dst_run_id: str,
    up_to_index_inclusive: int,
) -> Path:
    """
    在 workspace_root 下执行 run fork，并返回新 WAL 文件路径（events.jsonl）。

    参数：
    - workspace_root：工作区根目录
    - src_run_id：源 run_id
    - dst_run_id：目标 run_id（fork 后的新 run）
    - up_to_index_inclusive：包含的最大行号（0-based）
    """

    ws = Path(workspace_root).resolve()
    src_wal_path = (ws / ".skills_runtime_sdk" / "runs" / str(src_run_id) / "events.jsonl").resolve()
    dst_wal_path = (ws / ".skills_runtime_sdk" / "runs" / str(dst_run_id) / "events.jsonl").resolve()

    fork_run_events_jsonl(
        src_wal_path=src_wal_path,
        dst_wal_path=dst_wal_path,
        new_run_id=str(dst_run_id),
        up_to_index_inclusive=up_to_index_inclusive,
    )
    return dst_wal_path
