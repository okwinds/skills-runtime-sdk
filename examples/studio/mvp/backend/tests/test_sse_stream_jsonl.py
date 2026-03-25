import asyncio
import json
from pathlib import Path

from studio_api.sse import stream_jsonl_as_sse


class _DummyRequest:
    client = None

    async def is_disconnected(self) -> bool:
        return False


def test_stream_jsonl_as_sse_yields_events_and_stops(tmp_path: Path) -> None:
    jsonl = tmp_path / "events.jsonl"
    jsonl.write_text(
        "\n".join(
            [
                json.dumps({"type": "llm_response_delta", "payload": {"delta_type": "text", "text": "hi"}}),
                json.dumps({"type": "run_completed", "payload": {"final_output": "ok"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    async def _collect() -> list[str]:
        out: list[str] = []
        async for chunk in stream_jsonl_as_sse(request=_DummyRequest(), jsonl_path=jsonl, poll_interval_sec=0):
            out.append(chunk.decode("utf-8"))
        return out

    chunks = asyncio.run(_collect())
    joined = "".join(chunks)
    assert "event: llm_response_delta" in joined
    assert "event: run_completed" in joined
    # Ensure we produce valid SSE "data:" lines for each JSONL row
    assert "data: " in joined


def test_stream_jsonl_as_sse_handles_partial_json_line_then_recovers(tmp_path: Path) -> None:
    jsonl = tmp_path / "events.jsonl"
    jsonl.write_text("", encoding="utf-8")

    async def _collect() -> str:
        async def _writer() -> None:
            await asyncio.sleep(0.03)
            with jsonl.open("a", encoding="utf-8") as f:
                f.write('{"type":"run_completed"')
                f.flush()
            await asyncio.sleep(0.03)
            with jsonl.open("a", encoding="utf-8") as f:
                f.write(',"payload":{"final_output":"ok"}}\n')
                f.flush()

        async def _reader() -> str:
            async for chunk in stream_jsonl_as_sse(request=_DummyRequest(), jsonl_path=jsonl, poll_interval_sec=0.01):
                return chunk.decode("utf-8")
            return ""

        writer_task = asyncio.create_task(_writer())
        try:
            out = await asyncio.wait_for(_reader(), timeout=0.5)
        finally:
            await writer_task
        return out

    out = asyncio.run(_collect())
    assert "event: run_completed" in out
    assert '"final_output":"ok"' in out
