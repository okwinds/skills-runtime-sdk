"""
web_search 示例（离线，可回归）。

演示：
1) tools CLI 默认关闭 web_search（provider 不注入 -> fail-closed）
2) 产品侧注入 fake provider 后，可离线返回结构化 results
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from agent_sdk.tools.builtin import register_builtin_tools
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext, ToolRegistry


def _run_tools_cli(*, repo_root: Path, workspace_root: Path, argv: list[str], timeout_sec: int = 20) -> dict:
    """
    通过子进程调用 tools CLI，并返回 JSON payload。

    注意：
    - tools CLI 为了安全默认不注入 web_search provider，因此可用于“默认关闭行为”的验证。
    """

    src = repo_root / "packages" / "skills-runtime-sdk-python" / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    env["PYTHONUNBUFFERED"] = "1"

    p = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "agent_sdk.cli.main", "tools", *argv],
        cwd=str(workspace_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_sec,
    )
    if not p.stdout.strip():
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


class _FakeWebSearchProvider:
    """离线 fake provider（用于离线回归；不得联网）。"""

    def search(self, *, q: str, recency: int | None, limit: int) -> list[dict]:
        _ = recency
        # 返回结构稳定的假结果（不依赖外网）
        out = [
            {"title": f"Result for {q}", "url": "https://example.invalid", "snippet": "offline fake snippet"},
            {"title": "Second result", "url": "https://example.invalid/2", "snippet": "offline fake snippet 2"},
        ]
        return out[: max(1, int(limit))]


def main() -> int:
    """脚本入口：默认关闭 -> fake provider 启用。"""

    parser = argparse.ArgumentParser(description="tools_02_web_search_disabled_and_fake_provider")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    # repo_root = .../skills-runtime-sdk（本文件位于 examples/tools/<example>/run.py）
    repo_root = Path(__file__).resolve().parents[3]
    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    # 1) 默认关闭：tools CLI 不注入 provider，必须 fail-closed
    r1 = _run_tools_cli(
        repo_root=repo_root,
        workspace_root=workspace_root,
        argv=["web-search", "--q", "hello", "--limit", "3", "--workspace-root", str(workspace_root)],
    )
    assert r1["tool"] == "web_search"
    assert r1["result"]["ok"] is False
    assert r1["result"]["error_kind"] == "validation"
    assert r1["result"]["data"]["disabled"] is True
    print("[example] web_search_default_disabled=1")

    # 2) fake provider 启用（产品侧注入）
    ctx = ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="example_web_search",
        wal=None,
        emit_tool_events=False,
        web_search_provider=_FakeWebSearchProvider(),
    )
    registry = ToolRegistry(ctx=ctx)
    register_builtin_tools(registry)

    r2 = registry.dispatch(ToolCall(call_id="ex_web_search", name="web_search", args={"q": "hello", "limit": 2}))
    assert r2.ok is True
    assert isinstance(r2.details, dict)
    results = r2.details["data"]["results"]
    assert isinstance(results, list) and len(results) == 2
    assert results[0]["title"].startswith("Result for ")
    print("[example] web_search_fake_provider_ok=1")

    print("EXAMPLE_OK: tools_02_web_search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
