"""
examples/apps 共享支持代码（面向人类的应用示例）。

目标：
- 保持每个应用示例 `run.py` 足够短、可读；
- 提供离线/真模型两种运行路径的共同底座；
- 提供最小 UX：终端 HumanIOProvider 与终端 ApprovalProvider。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from skills_runtime.agent import Agent
from skills_runtime.config.defaults import load_default_config_dict
from skills_runtime.config.loader import load_config_dicts
from skills_runtime.llm.openai_chat import OpenAIChatCompletionsBackend
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.tools.protocol import HumanIOProvider


def repo_root_from_file(*, file: Path, parents: int) -> Path:
    """
    基于某个文件位置推导 repo_root。

    参数：
    - file：通常传 `Path(__file__)`
    - parents：向上回溯层数（见各 app 的注释）
    """

    return file.resolve().parents[parents]


def env_or_default(name: str, default: str) -> str:
    """读取环境变量（不存在则返回 default）。"""

    v = str(os.environ.get(name, "")).strip()
    return v if v else default


def write_overlay_for_app(
    *,
    workspace_root: Path,
    skills_root: Path,
    safety_mode: str,
    max_steps: int,
    enable_references: bool = False,
    enable_actions: bool = False,
    llm_base_url: Optional[str] = None,
    planner_model: Optional[str] = None,
    executor_model: Optional[str] = None,
) -> Path:
    """
    为单个 app 写入 overlay（runtime.yaml）。

    说明：
    - overlay 写在 workspace_root 下，便于“产物与配置同处一个 workspace”；
    - skills source root 使用绝对路径，避免工作目录变化导致扫描失败。
    """

    overlay = workspace_root / "runtime.yaml"
    cfg: Dict[str, Any] = {
        "run": {"max_steps": int(max_steps)},
        "safety": {
            "mode": str(safety_mode),
            "approval_timeout_ms": 60000,
            "tool_allowlist": ["read_file", "grep_files", "list_dir"],
        },
        "sandbox": {"default_policy": "none"},
        "skills": {
            "strictness": {
                "unknown_mention": "error",
                "duplicate_name": "error",
                "mention_format": "strict",
            },
            "references": {"enabled": bool(enable_references)},
            "actions": {"enabled": bool(enable_actions)},
            "spaces": [
                {
                    "id": "app-space",
                    "namespace": "examples:app",
                    "sources": ["app-fs"],
                    "enabled": True,
                }
            ],
            "sources": [
                {
                    "id": "app-fs",
                    "type": "filesystem",
                    "options": {"root": str(skills_root.resolve())},
                }
            ],
        },
    }

    if llm_base_url is not None:
        cfg["llm"] = {
            "base_url": str(llm_base_url),
            "api_key_env": "OPENAI_API_KEY",
            "timeout_sec": 60,
            "retry": {"max_retries": 2},
        }
    if planner_model is not None or executor_model is not None:
        models_cfg: Dict[str, str] = {}
        if planner_model is not None:
            models_cfg["planner"] = str(planner_model)
        if executor_model is not None:
            models_cfg["executor"] = str(executor_model)
        cfg["models"] = models_cfg

    overlay.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return overlay


def load_merged_config(*, config_paths: List[Path]) -> Any:
    """
    加载默认配置与 overlay，并返回合并后的配置对象（AgentSdkConfig）。

    注意：
    - 该 helper 仅用于构造真实模型 backend（OpenAICompatible）。
    """

    overlays: List[Dict[str, Any]] = [load_default_config_dict()]
    for p in config_paths:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            overlays.append(data)
    return load_config_dicts(overlays)


def build_openai_compatible_backend(*, config_paths: List[Path]) -> OpenAIChatCompletionsBackend:
    """基于合并配置构造 OpenAICompatible backend。"""

    merged = load_merged_config(config_paths=config_paths)
    return OpenAIChatCompletionsBackend(merged.llm)


class ScriptedApprovalProvider(ApprovalProvider):
    """按次数返回预置审批决策（用于离线回归与演示）。"""

    def __init__(self, decisions: List[ApprovalDecision]) -> None:
        self._decisions = list(decisions)
        self.calls: List[ApprovalRequest] = []

    async def request_approval(self, *, request: ApprovalRequest, timeout_ms: Optional[int] = None) -> ApprovalDecision:  # type: ignore[override]
        _ = timeout_ms
        self.calls.append(request)
        if self._decisions:
            return self._decisions.pop(0)
        return ApprovalDecision.DENIED


class TerminalApprovalProvider(ApprovalProvider):
    """
    终端交互式审批（用于真模型运行时的最小 UX）。

    约束：
    - 默认 fail-closed（用户未明确输入 y/Y 则拒绝）。
    """

    async def request_approval(self, *, request: ApprovalRequest, timeout_ms: Optional[int] = None) -> ApprovalDecision:  # type: ignore[override]
        _ = timeout_ms
        print("\n[approval] 需要审批：")
        print(f"- tool: {request.tool}")
        print(f"- summary: {request.summary}")
        if request.details:
            try:
                details_json = json.dumps(request.details, ensure_ascii=False, indent=2)
            except Exception:
                details_json = str(request.details)
            print("- details:")
            print(details_json)

        raw = input("[approval] 允许执行？(y/N)：").strip().lower()
        if raw in {"y", "yes"}:
            return ApprovalDecision.APPROVED_FOR_SESSION
        return ApprovalDecision.DENIED


class ScriptedHumanIO(HumanIOProvider):
    """离线 HumanIOProvider：按 question_id 返回预置答案。"""

    def __init__(self, answers_by_question_id: Dict[str, str]) -> None:
        self._answers = dict(answers_by_question_id)

    def request_human_input(
        self,
        *,
        call_id: str,
        question: str,
        choices: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_ms: Optional[int] = None,
    ) -> str:
        _ = (question, choices, context, timeout_ms)
        qid = str(call_id).split(":")[-1]
        if qid in self._answers:
            return str(self._answers[qid])
        # fail-closed：缺失则返回空串，暴露配置问题
        return ""


class TerminalHumanIO(HumanIOProvider):
    """终端交互式 HumanIOProvider（用于真模型运行时的最小 UX）。"""

    def request_human_input(
        self,
        *,
        call_id: str,
        question: str,
        choices: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_ms: Optional[int] = None,
    ) -> str:
        _ = (call_id, timeout_ms)
        header = ""
        if context and context.get("header"):
            header = str(context.get("header"))
        if header:
            print(f"\n[question] {header}")
        print(f"[question] {question}")

        if choices:
            print("[choices]")
            for idx, c in enumerate(list(choices), start=1):
                print(f"  {idx}. {c}")
            print("[hint] 你可以输入选项文本，或直接输入自定义值。")

        return input("[answer] ").strip()


def stream_events_with_min_ux(*, agent: Agent, task: str) -> Tuple[str, str]:
    """
    运行 run_stream 并打印“可感知过程”的最小 UX。

    返回：
    - (final_output, wal_locator)
    """

    final_output = ""
    wal_locator = ""
    t0 = time.monotonic()
    for ev in agent.run_stream(task):
        if ev.type in {"run_started", "run_completed"}:
            print(f"[event] {ev.type}")
        elif ev.type in {"skill_injected", "plan_updated", "approval_requested", "approval_decided"}:
            print(f"[event] {ev.type}")
        elif ev.type == "tool_call_started":
            payload = ev.payload or {}
            print(f"[tool] start {payload.get('tool')}")
        elif ev.type == "tool_call_finished":
            payload = ev.payload or {}
            tool = payload.get("tool")
            ok = (payload.get("result") or {}).get("ok")
            print(f"[tool] done {tool} ok={ok}")
        if ev.type == "run_completed":
            final_output = str((ev.payload or {}).get("final_output") or "")
            wal_locator = str((ev.payload or {}).get("wal_locator") or "")

    dt_ms = int((time.monotonic() - t0) * 1000)
    print(f"[done] wall_time_ms={dt_ms}")
    print(f"[done] wal_locator={wal_locator}")
    return (final_output, wal_locator)


def load_wal_events(*, wal_locator: str) -> List[Dict[str, Any]]:
    """
    读取 WAL（events.jsonl）并返回 JSON object 列表。

    参数：
    - wal_locator：run_completed 事件提供的定位符（通常是文件路径）
    """

    p = Path(str(wal_locator))
    if not p.exists():
        raise AssertionError(f"wal_locator does not exist: {wal_locator}")
    out: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def assert_event_exists(*, wal_locator: str, event_type: str) -> None:
    """断言 WAL 中存在至少 1 条指定事件类型。"""

    events = load_wal_events(wal_locator=wal_locator)
    if not any(ev.get("type") == event_type for ev in events):
        raise AssertionError(f"missing event type: {event_type}")


def assert_skill_injected(*, wal_locator: str, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    events = load_wal_events(wal_locator=wal_locator)
    for ev in events:
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def assert_tool_ok(*, wal_locator: str, tool: str) -> None:
    """断言 WAL 中存在至少 1 条指定 tool 的 ok tool_call_finished。"""

    events = load_wal_events(wal_locator=wal_locator)
    for ev in events:
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != tool:
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            return
    raise AssertionError(f"missing ok tool_call_finished for tool={tool}")
