from __future__ import annotations

from agent_sdk.tools.protocol import ToolSpec, tool_spec_to_openai_tool


def test_tool_spec_to_openai_tool_ignores_orchestration_metadata() -> None:
    spec = ToolSpec(
        name="shell_exec",
        description="exec",
        parameters={"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}}},
        requires_approval=True,
        sandbox_policy="restricted",
        idempotency="unsafe",
        output_schema={"type": "object"},
    )
    tool = tool_spec_to_openai_tool(spec)

    assert tool["type"] == "function"
    assert tool["function"]["name"] == "shell_exec"
    assert tool["function"]["description"] == "exec"
    assert tool["function"]["parameters"]["type"] == "object"
    # 必须只映射 function calling 需要的字段，不得把框架编排元数据泄漏到 provider 侧
    assert "requires_approval" not in tool["function"]
    assert "sandbox_policy" not in tool["function"]
    assert "idempotency" not in tool["function"]
    assert "output_schema" not in tool["function"]

