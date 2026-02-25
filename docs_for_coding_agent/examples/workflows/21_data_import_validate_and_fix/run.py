"""
数据导入校验与修复示例（Skills-First，离线可回归）。

演示：
- skills-first：任务包含 skill mention，触发 `skill_injected` 证据事件；
- `read_file`：读取 `input.csv`；
- `file_write`：落盘 `fixed.csv` / `validation_report.json` / `report.md`（走 approvals）；
- `shell_exec`：运行确定性 QA 校验（stdout 含 `QA_OK`，走 approvals）；
- WAL（events.jsonl）可用于审计与排障。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.tools.protocol import ToolCall


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
                "sandbox:",
                "  default_policy: none",
                "skills:",
                "  strictness:",
                "    unknown_mention: error",
                "    duplicate_name: error",
                "    mention_format: strict",
                "  spaces:",
                "    - id: wf-space",
                "      namespace: \"examples:workflow\"",
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

    for ev in _load_events(wal_locator):
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _get_shell_exec_stdout(*, wal_locator: str) -> str:
    """提取 `shell_exec` 的 tool_call_finished.result.stdout（用于断言 QA_OK）。"""

    for ev in _load_events(wal_locator):
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != "shell_exec":
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            return str(result.get("stdout") or "")
    raise AssertionError("missing ok tool_call_finished for tool=shell_exec")


def _write_input_csv(*, workspace_root: Path) -> str:
    """在 workspace 内生成包含错误的 input.csv，并返回其内容字符串。"""

    p = workspace_root / "input.csv"
    rows = [
        {"id": "1", "name": "Alice", "email": "", "age": "30"},  # 缺 email
        {"id": "2", "name": "Bob", "email": "bob@example.com", "age": "not_an_int"},  # age 非整数
        {"id": "2", "name": "Bob2", "email": "bob2@example.com", "age": "40"},  # 重复 id
        {"id": "3", "name": "Charlie", "email": "charlie@example.com", "age": "25"},
    ]
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "name", "email", "age"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p.read_text(encoding="utf-8")


def _build_fixed_outputs(*, input_csv_text: str) -> tuple[str, str]:
    """
    基于 input.csv 文本构造确定性的修复产物。

    返回：
    - fixed_csv：修复后的 CSV（以 \\n 结尾）
    - validation_report_json：校验报告 JSON（以 \\n 结尾）
    """

    reader = csv.DictReader(input_csv_text.splitlines())
    seen_ids: set[str] = set()

    fixed_rows: List[Dict[str, str]] = []
    issues: List[Dict[str, Any]] = []

    for raw in reader:
        rid = str(raw.get("id") or "").strip()
        if not rid:
            issues.append({"type": "missing_id_dropped", "raw": raw})
            continue
        if rid in seen_ids:
            issues.append({"type": "duplicate_id_dropped", "id": rid, "raw": raw})
            continue
        seen_ids.add(rid)

        name = str(raw.get("name") or "").strip()
        email = str(raw.get("email") or "").strip()
        age_raw = str(raw.get("age") or "").strip()

        if not email:
            email = f"unknown+{rid}@example.com"
            issues.append({"type": "missing_email_fixed", "id": rid})

        age: int
        try:
            age = int(age_raw)
        except Exception:
            age = 0
            issues.append({"type": "age_coerced_to_zero", "id": rid, "raw_age": age_raw})

        fixed_rows.append({"id": rid, "name": name, "email": email, "age": str(age)})

    fixed_csv_lines: List[str] = []
    fixed_csv_lines.append("id,name,email,age")
    for r in fixed_rows:
        fixed_csv_lines.append(f"{r['id']},{r['name']},{r['email']},{r['age']}")
    fixed_csv = "\n".join(fixed_csv_lines).rstrip() + "\n"

    report = {
        "input": {"file": "input.csv"},
        "output": {"fixed_csv": "fixed.csv"},
        "summary": {
            "input_rows": len(list(csv.DictReader(input_csv_text.splitlines()))),
            "output_rows": len(fixed_rows),
            "issues": len(issues),
        },
        "issues": issues,
        "rules": {
            "id_unique": True,
            "email_non_empty": True,
            "age_int": True,
            "age_invalid_coerce_to_zero": True,
            "email_missing_fill_unknown_plus_id": True,
            "duplicate_id_drop_later_rows": True,
        },
    }
    validation_report_json = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    return fixed_csv, validation_report_json


def _format_report_md(*, wal_locator: str, mention: str) -> str:
    """生成 report.md（确定性；QA 结果见 WAL）。"""

    return (
        "\n".join(
            [
                "# Data Import Validate & Fix Report",
                "",
                "## Workflow",
                f"- skill mention: `{mention}`",
                "- tools: `read_file` → `file_write`(fixed.csv/validation_report.json) → `shell_exec`(QA) → `file_write`(report.md)",
                "",
                "## Outputs",
                "- `input.csv`",
                "- `fixed.csv`",
                "- `validation_report.json`",
                "- `report.md`",
                "",
                "## Evidence (WAL)",
                f"- wal_locator: `{wal_locator}`",
                "",
                "说明：QA 校验结果（stdout 含 `QA_OK`）记录在 WAL 的 `tool_call_finished(tool=shell_exec)` 事件中。",
                "",
            ]
        ).rstrip()
        + "\n"
    )


def _build_backend(
    *,
    fixed_csv: str,
    validation_report_json: str,
    report_md: str,
    python_executable: str,
) -> FakeChatBackend:
    """Fake backend：按顺序触发 read_file → file_write*2 → shell_exec(QA) → file_write(report) → done。"""

    qa_code = "\n".join(
        [
            "import csv",
            "rows=list(csv.DictReader(open('fixed.csv', newline='')))",
            "ids=[r['id'] for r in rows]",
            "assert len(ids)==len(set(ids))",
            "for r in rows:",
            "    assert r.get('email') and ('@' in r['email'])",
            "    int(r.get('age') or '0')",
            "print('QA_OK')",
        ]
    )
    argv = [str(python_executable), "-c", qa_code]

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_read_input", name="read_file", args={"file_path": "input.csv", "offset": 1, "limit": 200})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_write_fixed", name="file_write", args={"path": "fixed.csv", "content": fixed_csv})],
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
                                call_id="tc_write_report_json",
                                name="file_write",
                                args={"path": "validation_report.json", "content": validation_report_json},
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
                                call_id="tc_shell_qa",
                                name="shell_exec",
                                args={"argv": argv, "timeout_ms": 5000, "sandbox": "none"},
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
                        tool_calls=[ToolCall(call_id="tc_write_report_md", name="file_write", args={"path": "report.md", "content": report_md})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已完成数据导入校验、自动修复与 QA 校验。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    """脚本入口：运行 workflows/21 示例（offline）。"""

    parser = argparse.ArgumentParser(description="21_data_import_validate_and_fix (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    run_id = "run_workflows_21_data_import_validate_and_fix"
    mention = "$[examples:workflow].data_import_fixer"

    # 示例必须自包含：skills 放在示例目录下，避免依赖 repo root。
    skills_root = (Path(__file__).resolve().parent / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    input_csv_text = _write_input_csv(workspace_root=workspace_root)
    fixed_csv, validation_report_json = _build_fixed_outputs(input_csv_text=input_csv_text)

    wal_locator = (workspace_root / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()
    report_md = _format_report_md(wal_locator=str(wal_locator), mention=mention)

    approval_provider = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # fixed.csv
            ApprovalDecision.APPROVED_FOR_SESSION,  # validation_report.json
            ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec(QA)
            ApprovalDecision.APPROVED_FOR_SESSION,  # report.md
        ]
    )

    backend = _build_backend(
        fixed_csv=fixed_csv,
        validation_report_json=validation_report_json,
        report_md=report_md,
        python_executable=sys.executable,
    )
    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )

    task = "\n".join(
        [
            f"请使用 {mention} 执行数据导入校验与修复：",
            "1) 读取 input.csv；",
            "2) 生成并写入 fixed.csv（修复缺失 email、age 非整数、重复 id）；",
            "3) 生成并写入 validation_report.json（列出问题与修复动作）；",
            "4) 运行 QA 校验（shell_exec，stdout 需包含 QA_OK）；",
            "5) 写 report.md（说明产物与 WAL 证据路径）。",
        ]
    )

    r = agent.run(task, run_id=run_id)

    # 离线门禁：产物必须存在
    for rel in ("input.csv", "fixed.csv", "validation_report.json", "report.md"):
        assert (workspace_root / rel).exists(), f"missing output file: {rel}"

    # 证据门禁：skills-first + QA stdout
    _assert_skill_injected(wal_locator=r.wal_locator, mention_text=mention)
    qa_stdout = _get_shell_exec_stdout(wal_locator=r.wal_locator)
    assert "QA_OK" in qa_stdout, f"QA stdout missing QA_OK: {qa_stdout!r}"

    print(f"[example] status={r.status}")
    print(f"[example] wal_locator={r.wal_locator}")
    print("[example] final_output:")
    print(r.final_output)
    print("EXAMPLE_OK: workflows_21")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
