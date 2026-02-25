from __future__ import annotations

import json

from skills_runtime.llm.chat_sse import iter_chat_completions_stream_events
from skills_runtime.llm.errors import ContextLengthExceededError


def test_chat_sse_tool_calls_arguments_aggregated_and_flushed() -> None:
    data_lines = [
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "file_read", "arguments": '{"path":"a'},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '.txt"}'},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "[DONE]",
    ]

    events = list(iter_chat_completions_stream_events(data_lines))
    tool_calls_events = [e for e in events if e.type == "tool_calls"]
    completed_events = [e for e in events if e.type == "completed"]

    assert len(tool_calls_events) == 1
    tool_calls = tool_calls_events[0].tool_calls
    assert tool_calls is not None
    assert len(tool_calls) == 1
    assert tool_calls[0].call_id == "call_1"
    assert tool_calls[0].name == "file_read"
    assert tool_calls[0].args == {"path": "a.txt"}
    assert tool_calls[0].raw_arguments == '{"path":"a.txt"}'

    assert len(completed_events) == 1
    assert completed_events[0].finish_reason == "done"


def test_chat_sse_tool_calls_out_of_order_indices_preserve_first_seen_order() -> None:
    data_lines = [
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 1,
                                    "id": "call_2",
                                    "type": "function",
                                    "function": {"name": "file_read", "arguments": '{"path":"b.txt"}'},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "file_read", "arguments": '{"path":"a.txt"}'},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ]

    events = list(iter_chat_completions_stream_events(data_lines))
    tool_calls_events = [e for e in events if e.type == "tool_calls"]
    assert len(tool_calls_events) == 1
    calls = tool_calls_events[0].tool_calls or []
    assert [c.call_id for c in calls] == ["call_2", "call_1"]


def test_chat_sse_tool_calls_missing_index_merges_by_id() -> None:
    data_lines = [
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "id": "call_x",
                                    "type": "function",
                                    "function": {"name": "file_read", "arguments": '{"path":"a'},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "id": "call_x",
                                    "type": "function",
                                    "function": {"arguments": '.txt"}'},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ]

    events = list(iter_chat_completions_stream_events(data_lines))
    calls = next(e.tool_calls for e in events if e.type == "tool_calls") or []
    assert len(calls) == 1
    assert calls[0].call_id == "call_x"
    assert calls[0].args == {"path": "a.txt"}
    assert calls[0].raw_arguments == '{"path":"a.txt"}'


def test_chat_sse_tool_calls_interleaved_fragments_by_index() -> None:
    data_lines = [
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "call_1", "type": "function", "function": {"name": "file_read", "arguments": '{"path":"a'}},
                                {"index": 1, "id": "call_2", "type": "function", "function": {"name": "file_read", "arguments": '{"path":"b'}},
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '.txt"}'}},
                                {"index": 1, "function": {"arguments": '.txt"}'}},
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ]

    events = list(iter_chat_completions_stream_events(data_lines))
    calls = next(e.tool_calls for e in events if e.type == "tool_calls") or []
    assert len(calls) == 2
    assert calls[0].call_id == "call_1"
    assert calls[0].args == {"path": "a.txt"}
    assert calls[1].call_id == "call_2"
    assert calls[1].args == {"path": "b.txt"}


def test_chat_sse_tool_calls_missing_index_and_id_uses_name_boundary_to_avoid_merge() -> None:
    data_lines = [
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"type": "function", "function": {"name": "file_read", "arguments": '{"path":"a.txt"}'}},
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"type": "function", "function": {"name": "file_read", "arguments": '{"path":"b.txt"}'}},
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ]

    events = list(iter_chat_completions_stream_events(data_lines))
    calls = next(e.tool_calls for e in events if e.type == "tool_calls") or []
    assert len(calls) == 2
    assert [c.args for c in calls] == [{"path": "a.txt"}, {"path": "b.txt"}]


def test_chat_sse_tool_calls_invalid_json_keeps_raw_arguments_and_empty_args() -> None:
    data_lines = [
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_bad",
                                    "type": "function",
                                    "function": {"name": "file_read", "arguments": '{"path":"a'},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ]

    events = list(iter_chat_completions_stream_events(data_lines))
    calls = next(e.tool_calls for e in events if e.type == "tool_calls") or []
    assert len(calls) == 1
    assert calls[0].call_id == "call_bad"
    assert calls[0].raw_arguments == '{"path":"a'
    assert calls[0].args == {}


def test_chat_sse_text_delta_emitted_and_stop_completes() -> None:
    data_lines = [
        json.dumps({"choices": [{"delta": {"content": "hello "}}]}),
        json.dumps({"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]}),
    ]

    events = list(iter_chat_completions_stream_events(data_lines))
    texts = [e.text for e in events if e.type == "text_delta"]
    completed = [e for e in events if e.type == "completed"]

    assert "".join([t or "" for t in texts]) == "hello world"
    assert len(completed) == 1
    assert completed[0].finish_reason == "stop"


def test_chat_sse_done_sentinel_variant_supported() -> None:
    events = list(iter_chat_completions_stream_events(["DONE"]))
    assert len(events) == 1
    assert events[0].type == "completed"
    assert events[0].finish_reason == "done"


def test_chat_sse_finish_reason_length_raises_context_length_exceeded() -> None:
    data_lines = [
        json.dumps({"choices": [{"delta": {}, "finish_reason": "length"}]}),
    ]

    try:
        _ = list(iter_chat_completions_stream_events(data_lines))
        assert False, "expected ContextLengthExceededError"
    except ContextLengthExceededError as e:
        assert "context_length_exceeded" in str(e)
