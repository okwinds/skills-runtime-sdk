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

