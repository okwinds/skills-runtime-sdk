from __future__ import annotations

from skills_runtime.skills.source_client_registry import SourceClientRegistry


def test_injected_clients_returns_copy() -> None:
    injected = {"src-pg": object()}
    registry = SourceClientRegistry(source_clients=injected)

    snap = registry.injected_clients
    snap["src-pg"] = object()
    snap["extra"] = object()

    snap2 = registry.injected_clients
    assert snap2["src-pg"] is injected["src-pg"]
    assert "extra" not in snap2
