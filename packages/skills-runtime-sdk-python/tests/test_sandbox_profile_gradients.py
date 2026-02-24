from __future__ import annotations

from agent_sdk.config.loader import load_config_dicts


def _base_cfg() -> dict:
    return {
        "config_version": 1,
        "run": {},
        "llm": {"base_url": "http://example.invalid/v1", "api_key_env": "X"},
        "models": {"planner": "p", "executor": "e"},
    }


def test_sandbox_profile_dev_expands_to_none_policy() -> None:
    cfg = load_config_dicts([_base_cfg() | {"sandbox": {"profile": "dev"}}])
    assert cfg.sandbox.profile == "dev"
    assert cfg.sandbox.default_policy == "none"
    assert cfg.sandbox.os.mode == "auto"
    assert cfg.sandbox.os.bubblewrap.unshare_net is True


def test_sandbox_profile_balanced_expands_to_restricted_policy() -> None:
    cfg = load_config_dicts([_base_cfg() | {"sandbox": {"profile": "balanced"}}])
    assert cfg.sandbox.profile == "balanced"
    assert cfg.sandbox.default_policy == "restricted"
    assert cfg.sandbox.os.mode == "auto"
    assert cfg.sandbox.os.bubblewrap.unshare_net is True
    assert cfg.sandbox.os.seatbelt.profile.strip() == "(version 1) (allow default)"


def test_sandbox_profile_prod_has_visible_seatbelt_deny_baseline() -> None:
    cfg = load_config_dicts([_base_cfg() | {"sandbox": {"profile": "prod"}}])
    assert cfg.sandbox.profile == "prod"
    assert cfg.sandbox.default_policy == "restricted"
    assert "deny file-read*" in cfg.sandbox.os.seatbelt.profile
    assert "/etc" in cfg.sandbox.os.seatbelt.profile


def test_sandbox_profile_is_macro_and_overrides_lower_fields() -> None:
    cfg = load_config_dicts(
        [
            _base_cfg()
            | {
                "sandbox": {
                    "default_policy": "none",
                    "os": {"bubblewrap": {"unshare_net": False}},
                    "profile": "balanced",
                }
            }
        ]
    )
    assert cfg.sandbox.default_policy == "restricted"
    assert cfg.sandbox.os.bubblewrap.unshare_net is True


def test_sandbox_unknown_profile_fails_fast() -> None:
    import pytest

    with pytest.raises(ValueError):
        load_config_dicts([_base_cfg() | {"sandbox": {"profile": "???unknown", "default_policy": "restricted"}}])
