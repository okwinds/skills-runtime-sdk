from __future__ import annotations

from typing import Any, Dict

import pytest
from pydantic import ValidationError

from skills_runtime.config.defaults import load_default_config_dict
from skills_runtime.config.loader import load_config_dicts


def test_sandbox_profile_preset_overrides_embedded_defaults() -> None:
    base = load_default_config_dict()
    overlay: Dict[str, Any] = {"config_version": 1, "sandbox": {"profile": "balanced"}}

    cfg = load_config_dicts([base, overlay])
    assert cfg.sandbox.profile == "balanced"
    assert cfg.sandbox.default_policy == "restricted"


def test_sandbox_profile_does_not_override_explicit_seatbelt_profile() -> None:
    base = load_default_config_dict()
    custom = '(version 1)\\n(deny file-read* (subpath "/etc"))\\n'
    overlay: Dict[str, Any] = {
        "config_version": 1,
        "sandbox": {"profile": "balanced", "os": {"seatbelt": {"profile": custom}}},
    }

    cfg = load_config_dicts([base, overlay])
    assert cfg.sandbox.profile == "balanced"
    assert cfg.sandbox.os.seatbelt.profile == custom


def test_sandbox_profile_does_not_override_explicit_default_policy() -> None:
    base = load_default_config_dict()
    overlay: Dict[str, Any] = {"config_version": 1, "sandbox": {"profile": "prod", "default_policy": "none"}}

    cfg = load_config_dicts([base, overlay])
    assert cfg.sandbox.profile == "prod"
    assert cfg.sandbox.default_policy == "none"


def test_unknown_sandbox_profile_still_fails_fast() -> None:
    base = load_default_config_dict()
    overlay: Dict[str, Any] = {"config_version": 1, "sandbox": {"profile": "custom"}}

    with pytest.raises(ValueError, match=r"sandbox\.profile must be one of"):
        load_config_dicts([base, overlay])


def test_sandbox_profile_preset_does_not_override_explicit_null() -> None:
    base = load_default_config_dict()
    overlay: Dict[str, Any] = {"config_version": 1, "sandbox": {"profile": "prod", "os": {"seatbelt": {"profile": None}}}}

    with pytest.raises(ValidationError):
        load_config_dicts([base, overlay])
