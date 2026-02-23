"""\
最小 RAG（离线 stub）示例（Skills-First，离线可回归）。\
\
演示：\
- skills-first：任务包含 skill mention，触发 `skill_injected`；\
- 自定义 tool：`kb_search`（关键词匹配，确定性）；\
- `file_write`：落盘 `retrieval.json` 与 `report.md`（走 approvals）；\
- WAL（events.jsonl）可用于审计与排障。\
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.tools.protocol import ToolCall


def _write_overlay(*, workspace_root: Path, skills_root: Path, safety_mode: str = "ask") -> Path:
    """写入示例运行用 overlay（runtime.yaml）。"""

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 30",
                "safety:",
                f"  mode: {json.dumps(safety_mode)}",
                "  approval_timeout_ms: 2000",
                "  tool_allowlist:",
                "    - kb_search",
                "sandbox:",
                "  default_policy: none",
                "skills:",
                "  mode: explicit",
                "  max_auto: 0",
                "  strictness:",
                "    unknown_mention: error",
                "    duplicate_name: error",
                "    mention_format: strict",
                "  spaces:",
                "    - id: wf-space",
                "      account: examples",
                "      domain: workflow",
                "      sources: [wf-fs]",
                "      enabled: true",
                "  sources:",
                "    - id: wf-fs",
                "      type: filesystem",
                "      options:",
                f"        root: {json.dumps(str(skills_root.resolve()))}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return overlay


class _ScriptedApprovalProvider(ApprovalProvider):
    """按次数返回预置审批决策（用于离线回归与演示）。"""

    def __init__(self, decisions: List[ApprovalDecision]) -> None:
        self._decisions = list(decisions)
        self.calls: List[ApprovalRequest] = []

    async def request_approval(
        self, *, request: ApprovalRequest, timeout_ms: Optional[int] = None
    ) -> ApprovalDecision:
        _ = timeout_ms
        self.calls.append(request)
        if self._decisions:
            return self._decisions.pop(0)
        return ApprovalDecision.DENIED


def _load_events(events_path: str) -> List[Dict[str, Any]]:
    """读取 WAL（events.jsonl）并返回 JSON object 列表。"""

    p = Path(events_path)
    if not p.exists():
        raise AssertionError(f"events_path does not exist: {events_path}")
    events: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def _assert_skill_injected(*, events_path: str, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    events = _load_events(events_path)
    for ev in events:
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _assert_tool_ok(*, events_path: str, tool_name: str) -> None:
    """断言 WAL 中存在某个 tool 的 tool_call_finished 且 ok=true。"""

    events = _load_events(events_path)
    for ev in events:
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != tool_name:
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            return
    raise AssertionError(f"missing ok tool_call_finished for tool={tool_name}")


def _build_backend(*, query: str, retrieval_json: str, report_md: str) -> FakeChatBackend:
    """Fake backend：先调用 kb_search，再落盘产物并输出回答。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_search",
                                name="kb_search",
                                args={"query": query, "top_k": 3},
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_write_retrieval",
                                name="file_write",
                                args={"path": "retrieval.json", "content": retrieval_json},
                            ),
                            ToolCall(
                                call_id="tc_write_report",
                                name="file_write",
                                args={"path": "report.md", "content": report_md},
                            ),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已完成离线检索并生成回答（见 report.md）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    """脚本入口：运行 minimal_rag_stub workflow（offline）。"""

    parser = argparse.ArgumentParser(description="17_minimal_rag_stub (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    corpus = [
        {"id": "dogs", "text": "Dogs can run fast and are loyal."},
        {"id": "cars", "text": "Sports cars can move really fast on highways."},
        {"id": "birds", "text": "Birds can fly; some species are very fast."},
        {"id": "books", "text": "Books contain knowledge."},
    ]

    query = "Things that can move really fast"

    def _kb_search_impl(*, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """离线 stub 检索：按关键词命中次数打分并返回 top_k（确定性）。"""

        tokens = [t for t in query.lower().replace("?", "").split() if t]
        scored: List[Dict[str, Any]] = []
        for doc in corpus:
            text = str(doc["text"]).lower()
            score = sum(1 for t in tokens if t in text)
            if score <= 0:
                continue
            scored.append({"id": doc["id"], "score": score, "snippet": doc["text"]})
        scored.sort(key=lambda d: (-int(d["score"]), str(d["id"])))
        return scored[: int(top_k)]

    approval_provider = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # retrieval.json
            ApprovalDecision.APPROVED_FOR_SESSION,  # report.md
        ]
    )

    retrieval = _kb_search_impl(query=query, top_k=3)
    retrieval_json = json.dumps({"query": query, "hits": retrieval}, ensure_ascii=False, indent=2) + "\n"

    best = retrieval[0]["id"] if retrieval else "(no_hit)"
    answer = f"根据离线检索，最相关的条目是：{best}。"
    report_md = "\n".join(
        [
            "# Minimal RAG Stub Report\n",
            f"## Query\n\n{query}\n",
            "## Retrieval\n",
            "```json\n" + retrieval_json.strip() + "\n```\n",
            "## Answer\n",
            answer + "\n",
        ]
    )
    if not report_md.endswith("\n"):
        report_md += "\n"

    backend = _build_backend(query=query, retrieval_json=retrieval_json, report_md=report_md)
    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )

    @agent.tool
    def kb_search(*, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """离线 stub 检索：按关键词命中次数打分并返回 top_k（确定性）。"""

        return _kb_search_impl(query=query, top_k=top_k)

    task = "\n".join(
        [
            "$[examples:workflow].rag_stub_runner",
            "请调用 kb_search 做离线检索，并落盘 retrieval.json 与 report.md。",
        ]
    )
    r = agent.run(task, run_id="run_workflows_17_minimal_rag_stub")

    assert r.status == "completed"
    assert (workspace_root / "retrieval.json").exists()
    assert (workspace_root / "report.md").exists()

    _assert_skill_injected(events_path=r.events_path, mention_text="$[examples:workflow].rag_stub_runner")
    _assert_tool_ok(events_path=r.events_path, tool_name="kb_search")
    _assert_tool_ok(events_path=r.events_path, tool_name="file_write")

    print("EXAMPLE_OK: workflows_17")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
