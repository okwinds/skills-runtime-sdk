from __future__ import annotations

from typing import Any, Dict

import pytest

from skills_runtime.config.loader import load_config_dicts


def _base_cfg() -> Dict[str, Any]:
    return {
        "config_version": 1,
        "run": {},
        "llm": {"base_url": "http://example.invalid/v1", "api_key_env": "X"},
        "models": {"planner": "p", "executor": "e"},
    }


def test_config_rejects_legacy_llm_max_retries_fails_fast() -> None:
    """legacy `llm.max_retries` 必须被拒绝（不得静默兼容）。"""

    cfg = _base_cfg() | {"llm": {"base_url": "http://example.invalid/v1", "api_key_env": "X", "max_retries": 3}}
    with pytest.raises(Exception):
        load_config_dicts([cfg])


def test_config_rejects_unknown_top_level_key_fails_fast() -> None:
    """未知 top-level key 必须被拒绝（extra=forbid）。"""

    cfg = _base_cfg() | {"llms": {"base_url": "http://typo.invalid/v1"}}
    with pytest.raises(Exception):
        load_config_dicts([cfg])

