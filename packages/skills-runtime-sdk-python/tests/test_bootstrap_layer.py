from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_load_dotenv_if_present_loads_workspace_root_dotenv(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".env").write_text("X_BOOT=1\n", encoding="utf-8")

    monkeypatch.delenv("SKILLS_RUNTIME_SDK_ENV_FILE", raising=False)
    monkeypatch.delenv("X_BOOT", raising=False)
    p, env = __import__("skills_runtime.bootstrap").bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)
    assert p is not None
    assert Path(p) == (ws / ".env")
    assert env == {"X_BOOT": "1"}
    assert os.environ.get("X_BOOT") is None


def test_load_dotenv_if_present_does_not_override_existing_env_by_default(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".env").write_text("X_BOOT=from_file\n", encoding="utf-8")

    monkeypatch.delenv("SKILLS_RUNTIME_SDK_ENV_FILE", raising=False)
    monkeypatch.setenv("X_BOOT", "from_process")
    _p, env = __import__("skills_runtime.bootstrap").bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)
    assert env == {}
    assert os.environ.get("X_BOOT") == "from_process"


def test_load_dotenv_if_present_override_true_overrides_existing_env(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".env").write_text("X_BOOT=from_file\n", encoding="utf-8")

    monkeypatch.delenv("SKILLS_RUNTIME_SDK_ENV_FILE", raising=False)
    monkeypatch.setenv("X_BOOT", "from_process")
    _p, env = __import__("skills_runtime.bootstrap").bootstrap.load_dotenv_if_present(workspace_root=ws, override=True)
    assert env == {"X_BOOT": "from_file"}
    assert os.environ.get("X_BOOT") == "from_process"


def test_load_dotenv_if_present_uses_skills_runtime_sdk_env_file_relative_to_workspace_root(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    envp = ws / "config" / "dev.env"
    envp.write_text("X_BOOT=2\n", encoding="utf-8")

    monkeypatch.setenv("SKILLS_RUNTIME_SDK_ENV_FILE", "config/dev.env")
    monkeypatch.delenv("X_BOOT", raising=False)
    p, env = __import__("skills_runtime.bootstrap").bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)
    assert p is not None
    assert Path(p) == envp
    assert env == {"X_BOOT": "2"}
    assert os.environ.get("X_BOOT") is None


def test_load_dotenv_if_present_missing_skills_runtime_sdk_env_file_fails_fast(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """env file 指定了但不存在时必须 fail-fast（不得静默忽略）。"""

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("SKILLS_RUNTIME_SDK_ENV_FILE", "config/missing.env")
    with pytest.raises(ValueError):
        __import__("skills_runtime.bootstrap").bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)


def test_load_dotenv_if_present_ignores_legacy_env_file_key(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """仅设置 legacy env key 时不得生效（必须忽略）。"""

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    envp = ws / "config" / "legacy.env"
    envp.write_text("X_BOOT=9\n", encoding="utf-8")

    monkeypatch.delenv("SKILLS_RUNTIME_SDK_ENV_FILE", raising=False)
    legacy_key = "AGENT" + "_SDK_ENV_FILE"
    monkeypatch.setenv(legacy_key, "config/legacy.env")
    monkeypatch.delenv("X_BOOT", raising=False)

    p, env = __import__("skills_runtime.bootstrap").bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)
    assert p is None
    assert env == {}
    assert os.environ.get("X_BOOT") is None


def test_load_dotenv_if_present_prefers_skills_runtime_sdk_env_file(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """同时设置 new + legacy 时，必须只使用 new。"""

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    env_new = ws / "config" / "new.env"
    env_old = ws / "config" / "old.env"
    env_new.write_text("X_BOOT=from_new\n", encoding="utf-8")
    env_old.write_text("X_BOOT=from_old\n", encoding="utf-8")

    monkeypatch.setenv("SKILLS_RUNTIME_SDK_ENV_FILE", "config/new.env")
    legacy_key = "AGENT" + "_SDK_ENV_FILE"
    monkeypatch.setenv(legacy_key, "config/old.env")
    monkeypatch.delenv("X_BOOT", raising=False)

    p, env = __import__("skills_runtime.bootstrap").bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)
    assert p is not None
    assert Path(p) == env_new
    assert env == {"X_BOOT": "from_new"}
    assert os.environ.get("X_BOOT") is None


def test_load_dotenv_parses_export_quotes_and_comments(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".env").write_text(
        "\n".join(
            [
                "# comment",
                "export A=1",
                "B='2'",
                'C=\"3\"',
                "D=4 # inline comment not supported but should not crash",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("SKILLS_RUNTIME_SDK_ENV_FILE", raising=False)
    for k in ["A", "B", "C", "D"]:
        monkeypatch.delenv(k, raising=False)

    _p, env = __import__("skills_runtime.bootstrap").bootstrap.load_dotenv_if_present(workspace_root=ws, override=False)
    assert env.get("A") == "1"
    assert env.get("B") == "2"
    assert env.get("C") == "3"
    assert str(env.get("D", "")).startswith("4")
    assert os.environ.get("A") is None


def test_discover_overlay_paths_reads_env_list_and_appends_default_runtime_yaml(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "a.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (ws / "config" / "runtime.yaml").write_text("config_version: 1\n", encoding="utf-8")

    monkeypatch.setenv("SKILLS_RUNTIME_SDK_CONFIG_PATHS", "a.yaml;config/runtime.yaml")
    paths = __import__("skills_runtime.bootstrap").bootstrap.discover_overlay_paths(workspace_root=ws)
    assert [p.name for p in paths] == ["runtime.yaml", "a.yaml"]


def test_discover_overlay_paths_deduplicates_by_canonical_path(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "runtime.yaml").write_text("config_version: 1\n", encoding="utf-8")

    monkeypatch.setenv("SKILLS_RUNTIME_SDK_CONFIG_PATHS", "config/runtime.yaml;./config/runtime.yaml")
    paths = __import__("skills_runtime.bootstrap").bootstrap.discover_overlay_paths(workspace_root=ws)
    assert len(paths) == 1
    assert paths[0] == (ws / "config" / "runtime.yaml").resolve()


def test_discover_overlay_paths_does_not_discover_legacy_llm_yaml(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "llm.yaml").write_text("config_version: 1\n", encoding="utf-8")

    monkeypatch.delenv("SKILLS_RUNTIME_SDK_CONFIG_PATHS", raising=False)
    paths = __import__("skills_runtime.bootstrap").bootstrap.discover_overlay_paths(workspace_root=ws)
    assert paths == []


def test_discover_overlay_paths_ignores_legacy_config_paths_env(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """仅设置 legacy config paths env 时不得生效（必须忽略）。"""

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "overlay.yaml").write_text("config_version: 1\n", encoding="utf-8")

    monkeypatch.delenv("SKILLS_RUNTIME_SDK_CONFIG_PATHS", raising=False)
    legacy_key = "AGENT" + "_SDK_CONFIG_PATHS"
    monkeypatch.setenv(legacy_key, "overlay.yaml")

    paths = __import__("skills_runtime.bootstrap").bootstrap.discover_overlay_paths(workspace_root=ws)
    assert paths == []


def test_discover_overlay_paths_prefers_runtime_yaml_when_both_exist(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "runtime.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (ws / "config" / "llm.yaml").write_text("config_version: 1\n", encoding="utf-8")

    monkeypatch.delenv("SKILLS_RUNTIME_SDK_CONFIG_PATHS", raising=False)
    paths = __import__("skills_runtime.bootstrap").bootstrap.discover_overlay_paths(workspace_root=ws)
    assert len(paths) == 1
    assert paths[0] == (ws / "config" / "runtime.yaml").resolve()


def test_resolve_effective_run_config_uses_yaml_when_no_env_or_session(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "runtime.yaml").write_text(
        "\n".join(
            [
                "config_version: 1",
                "llm:",
                "  base_url: http://yaml.test/v1",
                "  api_key_env: OPENAI_API_KEY",
                "models:",
                "  planner: yaml-planner",
                "  executor: yaml-executor",
                "",
            ]
        ),
        encoding="utf-8",
    )

    for k in [
        "SKILLS_RUNTIME_SDK_CONFIG_PATHS",
        "SKILLS_RUNTIME_SDK_PLANNER_MODEL",
        "SKILLS_RUNTIME_SDK_EXECUTOR_MODEL",
        "SKILLS_RUNTIME_SDK_LLM_BASE_URL",
        "SKILLS_RUNTIME_SDK_LLM_API_KEY_ENV",
    ]:
        monkeypatch.delenv(k, raising=False)

    cfg = __import__("skills_runtime.bootstrap").bootstrap.resolve_effective_run_config(workspace_root=ws, session_settings={"models": {}, "llm": {}})
    assert cfg.planner_model == "yaml-planner"
    assert cfg.executor_model == "yaml-executor"
    assert cfg.base_url == "http://yaml.test/v1"
    assert cfg.api_key_env == "OPENAI_API_KEY"
    assert cfg.sources["models.planner"].startswith("yaml:")


def test_resolve_effective_run_config_env_overrides_yaml(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "runtime.yaml").write_text(
        "\n".join(
            [
                "config_version: 1",
                "llm:",
                "  base_url: http://yaml.test/v1",
                "  api_key_env: OPENAI_API_KEY",
                "models:",
                "  planner: yaml-planner",
                "  executor: yaml-executor",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("SKILLS_RUNTIME_SDK_EXECUTOR_MODEL", "env-executor")
    monkeypatch.setenv("SKILLS_RUNTIME_SDK_LLM_BASE_URL", "http://env.test/v1")

    cfg = __import__("skills_runtime.bootstrap").bootstrap.resolve_effective_run_config(workspace_root=ws, session_settings={"models": {}, "llm": {}})
    assert cfg.executor_model == "env-executor"
    assert cfg.base_url == "http://env.test/v1"
    assert cfg.sources["models.executor"] == "env:SKILLS_RUNTIME_SDK_EXECUTOR_MODEL"
    assert cfg.sources["llm.base_url"] == "env:SKILLS_RUNTIME_SDK_LLM_BASE_URL"


def test_resolve_effective_run_config_ignores_legacy_env(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """仅设置 legacy env key 时不得生效（必须忽略）。"""

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "runtime.yaml").write_text(
        "\n".join(
            [
                "config_version: 1",
                "llm:",
                "  base_url: http://yaml.test/v1",
                "  api_key_env: OPENAI_API_KEY",
                "models:",
                "  planner: yaml-planner",
                "  executor: yaml-executor",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SKILLS_RUNTIME_SDK_EXECUTOR_MODEL", raising=False)
    monkeypatch.delenv("SKILLS_RUNTIME_SDK_LLM_BASE_URL", raising=False)
    legacy_exec = "AGENT" + "_SDK_EXECUTOR_MODEL"
    legacy_base_url = "AGENT" + "_SDK_LLM_BASE_URL"
    monkeypatch.setenv(legacy_exec, "env-executor")
    monkeypatch.setenv(legacy_base_url, "http://env.test/v1")

    cfg = __import__("skills_runtime.bootstrap").bootstrap.resolve_effective_run_config(workspace_root=ws, session_settings={"models": {}, "llm": {}})
    assert cfg.executor_model == "yaml-executor"
    assert cfg.base_url == "http://yaml.test/v1"
    assert cfg.sources["models.executor"].startswith("yaml:")
    assert cfg.sources["llm.base_url"].startswith("yaml:")


def test_resolve_effective_run_config_prefers_skills_runtime_sdk_env(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """同时设置 new + legacy 时，必须只使用 new。"""

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "runtime.yaml").write_text("config_version: 1\n", encoding="utf-8")

    monkeypatch.setenv("SKILLS_RUNTIME_SDK_PLANNER_MODEL", "env-planner-new")
    legacy_planner = "AGENT" + "_SDK_PLANNER_MODEL"
    monkeypatch.setenv(legacy_planner, "env-planner-old")
    cfg = __import__("skills_runtime.bootstrap").bootstrap.resolve_effective_run_config(
        workspace_root=ws,
        session_settings={"models": {}, "llm": {}},
    )
    assert cfg.planner_model == "env-planner-new"
    assert cfg.sources["models.planner"] == "env:SKILLS_RUNTIME_SDK_PLANNER_MODEL"


def test_resolve_effective_run_config_session_overrides_env(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "runtime.yaml").write_text("config_version: 1\n", encoding="utf-8")

    monkeypatch.setenv("SKILLS_RUNTIME_SDK_PLANNER_MODEL", "env-planner")
    cfg = __import__("skills_runtime.bootstrap").bootstrap.resolve_effective_run_config(
        workspace_root=ws,
        session_settings={"models": {"planner": "session-planner"}, "llm": {}},
    )
    assert cfg.planner_model == "session-planner"
    assert cfg.sources["models.planner"] == "session_settings:models.planner"


def test_resolve_effective_run_config_reports_overlay_yaml_source(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    base = ws / "config" / "runtime.yaml"
    base.write_text(
        "\n".join(["config_version: 1", "models:", "  executor: base-executor", ""]),
        encoding="utf-8",
    )
    overlay = ws / "overlay.yaml"
    overlay.write_text(
        "\n".join(["config_version: 1", "models:", "  executor: overlay-executor", ""]),
        encoding="utf-8",
    )

    monkeypatch.setenv("SKILLS_RUNTIME_SDK_CONFIG_PATHS", str(overlay))
    monkeypatch.delenv("SKILLS_RUNTIME_SDK_EXECUTOR_MODEL", raising=False)

    cfg = __import__("skills_runtime.bootstrap").bootstrap.resolve_effective_run_config(workspace_root=ws, session_settings={"models": {}, "llm": {}})
    assert cfg.executor_model == "overlay-executor"
    assert "overlay:" in cfg.sources["models.executor"]


def test_resolve_effective_run_config_missing_overlay_raises_value_error(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SKILLS_RUNTIME_SDK_CONFIG_PATHS", "missing.yaml")
    with pytest.raises(ValueError):
        __import__("skills_runtime.bootstrap").bootstrap.resolve_effective_run_config(workspace_root=ws, session_settings={"models": {}, "llm": {}})
