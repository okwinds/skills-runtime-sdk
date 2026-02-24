"""
view_image 离线示例（Skills-First，离线可回归）。

演示：
- skills-first：任务包含 skill mention，触发 `skill_injected` 证据事件；
- `view_image`：读取 workspace 内生成的 PNG，并返回 mime/bytes/base64；
- `file_write`：落盘 `image_meta.json` 与 `report.md`（走 approvals）；
- WAL（events.jsonl）可用于审计与排障。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.tools.protocol import ToolCall

_PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X2Z0AAAAASUVORK5CYII="
)


def _write_overlay(*, workspace_root: Path, skills_root: Path, safety_mode: str = "ask") -> Path:
    """写入示例运行用 overlay（runtime.yaml）。

    参数：
    - workspace_root：工作区根目录
    - skills_root：filesystem skills root（本示例目录下 `skills/`）
    - safety_mode：allow|ask|deny（示例默认 ask）
    """

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 20",
                "safety:",
                f"  mode: {json.dumps(safety_mode)}",
                "  approval_timeout_ms: 2000",
                "sandbox:",
                "  default_policy: none",
                "skills:",
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

    async def request_approval(self, *, request: ApprovalRequest, timeout_ms: Optional[int] = None) -> ApprovalDecision:
        _ = timeout_ms
        self.calls.append(request)
        if self._decisions:
            return self._decisions.pop(0)
        return ApprovalDecision.DENIED


def _load_events(wal_locator: str) -> List[Dict[str, Any]]:
    """读取 WAL（events.jsonl）并返回 JSON object 列表。"""

    p = Path(wal_locator)
    if not p.exists():
        raise AssertionError(f"wal_locator does not exist: {wal_locator}")
    events: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def _assert_skill_injected(*, wal_locator: str, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    events = _load_events(wal_locator)
    for ev in events:
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _assert_tool_ok(*, wal_locator: str, tool_name: str) -> None:
    """断言 WAL 中存在某个 tool 的 tool_call_finished 且 ok=true。"""

    events = _load_events(wal_locator)
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


def _get_view_image_result_data(*, wal_locator: str) -> Dict[str, Any]:
    """提取 `view_image` 的 tool_call_finished.result.data（用于断言返回的 base64/mime/bytes）。"""

    events = _load_events(wal_locator)
    for ev in events:
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != "view_image":
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            data = result.get("data") or {}
            if isinstance(data, dict):
                return data
    raise AssertionError("missing ok tool_call_finished for tool=view_image")


def _build_backend(*, image_meta_json: str, report_md: str) -> FakeChatBackend:
    """构造 Fake backend：调用 view_image，然后落盘 image_meta/report。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_view", name="view_image", args={"path": "generated.png"})],
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
                                call_id="tc_write_meta",
                                name="file_write",
                                args={"path": "image_meta.json", "content": image_meta_json},
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
                    ChatStreamEvent(type="text_delta", text="已离线读取图片并落盘产物（见 image_meta.json/report.md）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    """脚本入口：运行 view_image_offline workflow（offline）。"""

    parser = argparse.ArgumentParser(description="19_view_image_offline (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    run_id = "run_workflows_19_view_image_offline"
    mention = "$[examples:workflow].view_image_offline_runner"

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    generated_png = workspace_root / "generated.png"
    raw = base64.b64decode(_PNG_1X1_BASE64)
    generated_png.write_bytes(raw)

    sha256 = hashlib.sha256(raw).hexdigest()
    wal_locator = (workspace_root / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()

    image_meta = {
        "image_relpath": "generated.png",
        "image_abspath": str(generated_png.resolve()),
        "mime": "image/png",
        "bytes": len(raw),
        "sha256": sha256,
        "base64": base64.b64encode(raw).decode("ascii"),
        "wal_locator": str(wal_locator),
        "skill_mention": mention,
    }
    image_meta_json = json.dumps(image_meta, ensure_ascii=False, indent=2) + "\n"

    report_md = "\n".join(
        [
            "# View Image Offline Report\n",
            "## Input\n",
            f"- image: `generated.png` (sha256={sha256}, bytes={len(raw)})\n",
            "## Workflow\n",
            f"- skill mention: `{mention}`\n",
            "- tools: `view_image` → `file_write` (image_meta.json/report.md)\n",
            "## Evidence (WAL)\n",
            f"- wal_locator: `{wal_locator}`\n",
            "## Outputs\n",
            "- `generated.png`\n",
            "- `image_meta.json`\n",
            "- `report.md`\n",
        ]
    )
    if not report_md.endswith("\n"):
        report_md += "\n"

    approval_provider = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # image_meta.json
            ApprovalDecision.APPROVED_FOR_SESSION,  # report.md
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=_build_backend(image_meta_json=image_meta_json, report_md=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )

    task = "\n".join(
        [
            mention,
            "请调用 view_image 读取 generated.png，确认 mime/bytes/base64，并落盘 image_meta.json 与 report.md。",
        ]
    )
    r = agent.run(task, run_id=run_id)

    assert r.status == "completed"
    assert generated_png.exists()
    assert (workspace_root / "image_meta.json").exists()
    assert (workspace_root / "report.md").exists()

    _assert_skill_injected(wal_locator=r.wal_locator, mention_text=mention)
    _assert_tool_ok(wal_locator=r.wal_locator, tool_name="view_image")
    _assert_tool_ok(wal_locator=r.wal_locator, tool_name="file_write")

    meta_loaded = json.loads((workspace_root / "image_meta.json").read_text(encoding="utf-8"))
    assert meta_loaded["sha256"] == sha256
    assert meta_loaded["mime"] == "image/png"
    assert int(meta_loaded["bytes"]) == len(raw)

    view_data = _get_view_image_result_data(wal_locator=r.wal_locator)
    assert view_data["mime"] == "image/png"
    assert int(view_data["bytes"]) == len(raw)
    assert base64.b64decode(view_data["base64"]) == raw
    assert str(workspace_root) in str(view_data["path"])

    print("EXAMPLE_OK: workflows_19")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
