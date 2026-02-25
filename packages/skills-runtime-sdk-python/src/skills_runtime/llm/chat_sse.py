"""
Chat Completions Streaming SSE 解析器（Phase 2）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/llm-backend.md` §3（Streaming SSE）
- Codex 参考：`../codex/docs/workdocjcl/spec/05_Integrations/CHAT_WIRE_MAPPING.md` §11.2/§11.5

实现边界（Phase 2）：
- 支持终止哨兵：`[DONE]` 与 `DONE`
- 支持 `choices[].delta.content` 文本增量
- 支持 `choices[].delta.tool_calls[]` 的 arguments 分片拼接，并在 `finish_reason="tool_calls"` 时 flush
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional

from skills_runtime.llm.errors import ContextLengthExceededError
from skills_runtime.tools.protocol import ToolCall


@dataclass(frozen=True)
class ChatStreamEvent:
    """
    Chat streaming 解析输出事件（内部使用）。

    type:
    - `text_delta`：assistant 文本增量
    - `tool_calls`：一个批次的 tool calls（flush 后输出）
    - `completed`：流已完成（stop/[DONE]/EOF）
    """

    type: str
    text: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: Optional[str] = None


@dataclass
class _ToolCallState:
    """
    tool_call 分片拼接的内部状态（单个 call）。

    说明：
    - `arguments` 为 OpenAI wire 中 `function.arguments` 的字符串拼接结果（可能是分片）。
    - 在 flush 时会尝试 `json.loads(arguments)` 生成 `ToolCall.args`：
      - 仅接受 JSON object；否则按空对象处理（上层必须 fail-closed，不得执行）。
    """

    id: Optional[str] = None
    name: Optional[str] = None
    arguments: str = ""


class ChatCompletionsSseParser:
    """
    OpenAI-compatible chat.completions SSE parser（仅处理 data: JSON 的 payload）。

    用法：
    - 每次收到一条 `data: ...` 的 data 字符串，调用 `feed_data(data)`，获取 0..N 个 `ChatStreamEvent`
    - 流结束或收到 `[DONE]` 时，应保证最终会得到 `completed` 事件
    """

    def __init__(self) -> None:
        """初始化解析器的 tool_call 拼接状态与 completed 哨兵标记。"""

        self._tool_calls: Dict[int, _ToolCallState] = {}
        self._tool_call_order: List[int] = []
        self._tool_call_index_by_id: Dict[str, int] = {}
        self._last_tool_call_index: Optional[int] = None
        self._next_tool_call_index: int = 0
        self._completed_sent: bool = False

    def feed_data(self, data: str) -> List[ChatStreamEvent]:
        """
        处理单条 SSE data 字符串，返回解析得到的事件列表。

        说明：
        - 对 JSON 解析失败的 data：跳过（返回空列表），不终止
        - 对 `[DONE]` / `DONE`：会 flush 未完成的 tool_calls（若存在）并返回 completed
        """

        data_s = (data or "").strip()
        if not data_s:
            return []

        if data_s in ("[DONE]", "DONE"):
            events: List[ChatStreamEvent] = []
            if self._tool_calls:
                events.append(ChatStreamEvent(type="tool_calls", tool_calls=self._flush_tool_calls(), finish_reason="done"))
            if not self._completed_sent:
                events.append(ChatStreamEvent(type="completed", finish_reason="done"))
                self._completed_sent = True
            return events

        try:
            obj = json.loads(data_s)
        except Exception:
            return []

        choices = obj.get("choices")
        if not isinstance(choices, list):
            return []

        out: List[ChatStreamEvent] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if isinstance(delta, dict):
                out.extend(self._handle_delta(delta))

            finish_reason = choice.get("finish_reason")
            if finish_reason == "tool_calls":
                out.append(ChatStreamEvent(type="tool_calls", tool_calls=self._flush_tool_calls(), finish_reason="tool_calls"))
            elif finish_reason == "stop":
                if not self._completed_sent:
                    out.append(ChatStreamEvent(type="completed", finish_reason="stop"))
                    self._completed_sent = True
            elif finish_reason == "length":
                # Phase 2：让上层决定如何处理（通常需要压缩/截断历史）
                raise ContextLengthExceededError("context_length_exceeded")

        return out

    def finish(self) -> List[ChatStreamEvent]:
        """
        在底层 stream EOF 时调用，确保发出 completed 事件。

        注意：
        - 某些 provider 不会发送 `[DONE]`，因此不能依赖哨兵。
        """

        if self._completed_sent:
            return []
        events: List[ChatStreamEvent] = []
        if self._tool_calls:
            events.append(ChatStreamEvent(type="tool_calls", tool_calls=self._flush_tool_calls(), finish_reason="eof"))
        events.append(ChatStreamEvent(type="completed", finish_reason="eof"))
        self._completed_sent = True
        return events

    def _handle_delta(self, delta: Dict[str, Any]) -> List[ChatStreamEvent]:
        """
        解析单个 `choices[].delta` 并产出 0..N 个事件。

        处理范围：
        - `delta.content`：文本增量（str 或 content blocks）
        - `delta.tool_calls[]`：工具调用增量（只累积；flush 在 finish_reason/tool_calls 或 [DONE]/EOF）
        """

        out: List[ChatStreamEvent] = []

        content = delta.get("content")
        if isinstance(content, str) and content:
            out.append(ChatStreamEvent(type="text_delta", text=content))
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        out.append(ChatStreamEvent(type="text_delta", text=text))

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if isinstance(tool_call, dict):
                    self._accumulate_tool_call_delta(tool_call)

        return out

    def _alloc_index(self) -> int:
        """分配一个未被占用的 tool_call 索引（用于 provider 未提供 index/id 的情况）。"""

        while self._next_tool_call_index in self._tool_calls:
            self._next_tool_call_index += 1
        idx = self._next_tool_call_index
        self._next_tool_call_index += 1
        return idx

    def _resolve_index(self, tool_call_delta: Dict[str, Any]) -> int:
        """
        为单条 `tool_call` 增量解析出“应当累积到哪个索引”的决定。

        优先级：
        1) `tool_call_delta.index`（若为 int）
        2) `tool_call_delta.id`（若曾出现过，复用历史映射）
        3) `tool_call_delta.id`（若为新 id 且缺 index：视为新 call，分配新 index）
        4) `self._last_tool_call_index`（尽量把连续分片拼到同一 call）
        5) 新分配 index

        额外启发式：
        - 当 provider 未提供 index/id，但携带 `function.name` 时，通常意味着“新 call 的开始”。
          若上一条 call 已经有 name，则分配新 index，避免两个 call 被错误拼接到一起。
        """

        index_val = tool_call_delta.get("index")
        idx: Optional[int] = None
        if isinstance(index_val, int):
            idx = index_val

        call_id = tool_call_delta.get("id")
        if isinstance(call_id, str) and call_id in self._tool_call_index_by_id:
            idx = self._tool_call_index_by_id[call_id]

        if idx is None:
            # 若 id 存在但未出现过，且 provider 没有给 index：更可能是“新 tool call”，而不是上一条的延续。
            if isinstance(call_id, str) and call_id:
                idx = self._alloc_index()
            else:
                idx = self._last_tool_call_index

        # 启发式：缺 index 且缺 id 时，若出现 function.name 且上一条 call 已经有 name，则认为是新 call。
        if idx is not None and not isinstance(index_val, int) and not (isinstance(call_id, str) and call_id):
            fn = tool_call_delta.get("function")
            if isinstance(fn, dict):
                name = fn.get("name")
                if isinstance(name, str) and name:
                    last_idx = self._last_tool_call_index
                    last_state = self._tool_calls.get(last_idx) if last_idx is not None else None
                    if last_state is not None and last_state.name:
                        idx = self._alloc_index()

        if idx is None:
            idx = self._alloc_index()

        if isinstance(call_id, str) and call_id and call_id not in self._tool_call_index_by_id:
            self._tool_call_index_by_id[call_id] = idx

        self._last_tool_call_index = idx
        return idx

    def _accumulate_tool_call_delta(self, tool_call_delta: Dict[str, Any]) -> None:
        """把一条 tool_call 增量累积进内部状态（id/name/arguments 分片）。"""

        idx = self._resolve_index(tool_call_delta)
        if idx not in self._tool_calls:
            self._tool_calls[idx] = _ToolCallState()
            self._tool_call_order.append(idx)

        state = self._tool_calls[idx]

        call_id = tool_call_delta.get("id")
        if isinstance(call_id, str) and call_id and state.id is None:
            state.id = call_id

        fn = tool_call_delta.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str) and name and state.name is None:
                state.name = name

            args_part = fn.get("arguments")
            if isinstance(args_part, str) and args_part:
                state.arguments += args_part

    def _flush_tool_calls(self) -> List[ToolCall]:
        """
        将当前累积的 tool_calls 输出为 `ToolCall` 列表并清空内部状态。

        约束：
        - 若某个 call 缺少 `name`，则跳过该 call（避免产生不可执行的 ToolCall）。
        - `raw_arguments` 保留原始字符串，便于上层回注到 wire。
        """

        tool_calls: List[ToolCall] = []
        for idx in self._tool_call_order:
            st = self._tool_calls.get(idx)
            if st is None:
                continue
            if not st.name:
                continue

            call_id = st.id or f"tool-call-{idx}"
            raw_args = st.arguments or ""
            try:
                parsed = json.loads(raw_args) if raw_args.strip() else {}
                args_obj = parsed if isinstance(parsed, dict) else {}
            except Exception:
                args_obj = {}

            tool_calls.append(ToolCall(call_id=call_id, name=st.name, args=args_obj, raw_arguments=raw_args))

        self._tool_calls.clear()
        self._tool_call_order.clear()
        self._tool_call_index_by_id.clear()
        self._last_tool_call_index = None
        return tool_calls


def iter_chat_completions_stream_events(data_lines: Iterable[str]) -> Iterator[ChatStreamEvent]:
    """
    便捷函数：将一组 `data:` payload 行解析为事件流。

    参数：
    - data_lines：每条都是 SSE 中 `data: ...` 的 `...` 部分（不含前缀）
    """

    parser = ChatCompletionsSseParser()
    for data in data_lines:
        for ev in parser.feed_data(data):
            yield ev
    for ev in parser.finish():
        yield ev
