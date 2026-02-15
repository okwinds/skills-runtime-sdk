"""
sandbox 证据字段示例（离线可回归）。

说明：
- 本示例不强依赖 seatbelt/bwrap 的可用性；
- 通过“restricted 但 adapter 缺失”的路径稳定演示 `data.sandbox` 字段；
- 真实沙箱效果的验证请用仓库脚本（见 README）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_sdk.core.executor import Executor
from agent_sdk.tools.builtin import register_builtin_tools
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext, ToolRegistry


def _print_sandbox(result_details: dict) -> None:
    data = (result_details or {}).get("data") or {}
    sandbox = data.get("sandbox") or {}
    print("[example] sandbox:", json.dumps(sandbox, ensure_ascii=False, sort_keys=True))


def main() -> int:
    """脚本入口：演示 data.sandbox meta 与 fail-closed 语义。"""

    parser = argparse.ArgumentParser(description="04_sandbox_evidence_and_verification (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    ctx_none = ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="run_example_step_04_none",
        executor=Executor(),
        sandbox_policy_default="none",
        sandbox_adapter=None,
    )
    reg_none = ToolRegistry(ctx=ctx_none)
    register_builtin_tools(reg_none)

    r1 = reg_none.dispatch(ToolCall(call_id="tc1", name="shell_exec", args={"argv": ["echo", "hi"], "sandbox": "inherit"}))
    assert r1.ok is True
    _print_sandbox(r1.details or {})

    # restricted + adapter 缺失：必须 fail-closed（sandbox_denied），并携带 sandbox meta
    ctx_restricted = ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="run_example_step_04_restricted",
        executor=Executor(),
        sandbox_policy_default="restricted",
        sandbox_adapter=None,
    )
    reg_restricted = ToolRegistry(ctx=ctx_restricted)
    register_builtin_tools(reg_restricted)

    r2 = reg_restricted.dispatch(
        ToolCall(call_id="tc2", name="shell_exec", args={"argv": ["echo", "hi"], "sandbox": "inherit"})
    )
    assert r2.ok is False
    assert r2.error_kind == "sandbox_denied"
    _print_sandbox(r2.details or {})

    print("EXAMPLE_OK: step_by_step_04")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

