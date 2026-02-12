from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import Request


def _format_sse_event(*, event: str, data_json: str) -> str:
    """
    格式化一条 SSE 消息。

    约束：
    - event：事件名（字符串）
    - data：单行 JSON 字符串
    """

    return f"event: {event}\n" f"data: {data_json}\n\n"


def stream_jsonl_as_sse(
    *,
    request: Request,
    jsonl_path: Path,
    poll_interval_sec: float = 0.2,
    terminal_events: Optional[set[str]] = None,
) -> AsyncIterator[bytes]:
    """
    将 JSONL 文件（每行一个 JSON object）转换为 SSE 流并 tail-follow。

    行格式约定：
    - JSON object 至少包含 `type` 字段（作为 SSE event 名）
    - 整行 JSON 作为 `data` 原样发送（不做字段重写）

    终止条件：
    - 读到 terminal event（默认 run_completed/run_failed/run_cancelled）
    - 或客户端断开连接
    """

    terminal = terminal_events or {"run_completed", "run_failed", "run_cancelled"}
    p = Path(jsonl_path)

    async def _gen() -> AsyncIterator[bytes]:
        offset = 0
        seen_terminal = False

        while True:
            if request.client is None:
                # 保守：无 client 信息时仍继续
                pass
            try:
                if await request.is_disconnected():
                    return
            except Exception:
                # fail-open：断连检测异常不阻断
                pass

            if p.exists():
                with p.open("r", encoding="utf-8") as f:
                    f.seek(offset)
                    while True:
                        line = f.readline()
                        if not line:
                            offset = f.tell()
                            break
                        raw = line.strip()
                        if not raw:
                            offset = f.tell()
                            continue
                        try:
                            obj = json.loads(raw)
                        except Exception:
                            offset = f.tell()
                            continue
                        ev_type = str(obj.get("type") or "message")
                        yield _format_sse_event(event=ev_type, data_json=raw).encode("utf-8")
                        if ev_type in terminal:
                            seen_terminal = True
                        offset = f.tell()

            if seen_terminal:
                return
            await asyncio.sleep(float(poll_interval_sec))

    return _gen()
