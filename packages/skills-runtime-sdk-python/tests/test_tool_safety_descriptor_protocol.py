"""ToolSafetyDescriptor Protocol 契约测试。"""


def test_protocol_is_runtime_checkable():
    from skills_runtime.tools.protocol import ToolSafetyDescriptor

    class FakeDescriptor:
        @property
        def policy_category(self) -> str:
            return "none"

        def extract_risk(self, args):
            from skills_runtime.safety.guard import CommandRisk

            return [], CommandRisk(risk_level="low", reason="")

        def sanitize_for_approval(self, args, **ctx):
            return "", {}

        def sanitize_for_event(self, args, redaction_values=()):
            return {}

    assert isinstance(FakeDescriptor(), ToolSafetyDescriptor)


def test_passthrough_descriptor_policy_category():
    from skills_runtime.tools.protocol import PassthroughDescriptor, ToolSafetyDescriptor

    d = PassthroughDescriptor()
    assert d.policy_category == "none"
    assert isinstance(d, ToolSafetyDescriptor)


def test_passthrough_descriptor_extract_risk():
    from skills_runtime.tools.protocol import PassthroughDescriptor

    d = PassthroughDescriptor()
    result = d.extract_risk({"foo": "bar"})
    assert isinstance(result, dict)
    assert result["argv"] == []
    assert result["risk_level"] == "low"
    assert "reason" in result


def test_passthrough_descriptor_sanitize_for_approval():
    from skills_runtime.tools.protocol import PassthroughDescriptor

    d = PassthroughDescriptor()
    result = d.sanitize_for_approval({"a": 1, "b": 2})
    assert isinstance(result, dict)
    assert result == {"a": 1, "b": 2}


def test_passthrough_descriptor_sanitize_for_event():
    from skills_runtime.tools.protocol import PassthroughDescriptor

    d = PassthroughDescriptor()
    result = d.sanitize_for_event({"x": "secret"}, redaction_values=["secret"])
    assert isinstance(result, dict)
    assert result == {"x": "secret"}
