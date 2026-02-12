from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
import yaml


def _write_yaml(path: Path, obj: Dict[str, Any]) -> Path:
    """写入 YAML overlay（根节点必须为 mapping）。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(obj, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _write_skill(dir_path: Path, *, name: str, description: str, body: str = "body\n") -> Path:
    """写入最小 SKILL.md fixture。"""

    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / "SKILL.md"
    p.write_text(
        "\n".join(["---", f"name: {name}", f'description: \"{description}\"', "---", body.rstrip("\n"), ""]),
        encoding="utf-8",
    )
    return p


def _run_cli(args: list[str], capsys) -> Tuple[int, Dict[str, Any], str]:  # type: ignore[no-untyped-def]
    """运行 CLI 并返回 (exit_code, parsed_json, raw_stdout)。"""

    from agent_sdk.cli.main import main

    code = main(args)
    out = capsys.readouterr().out
    assert out.endswith("\n")
    return code, json.loads(out), out


def _clear_bootstrap_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """清理 bootstrap 相关 env，避免本机环境变量影响测试。"""

    for k in [
        "SKILLS_RUNTIME_SDK_CONFIG_PATHS",
        "SKILLS_RUNTIME_SDK_ENV_FILE",
        "AGENT_SDK_CONFIG_PATHS",
        "AGENT_SDK_ENV_FILE",
    ]:
        monkeypatch.delenv(k, raising=False)


# --------------------
# skills preflight
# --------------------


def test_cli_preflight_default_config_ok(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """preflight 默认配置应输出空 issues 且 exit 0。"""

    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(["skills", "preflight", "--workspace-root", str(tmp_path)], capsys)
    assert code == 0
    assert obj["issues"] == []
    assert obj["stats"]["issues_total"] == 0
    assert obj["stats"]["errors_total"] == 0
    assert obj["stats"]["warnings_total"] == 0
    assert obj["stats"]["overlay_paths"] == []
    assert obj["stats"]["env_file"] is None
    assert Path(obj["stats"]["workspace_root"]).is_absolute()


def test_cli_preflight_pretty_output_is_valid_json(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """--pretty 仅影响格式，不影响可解析性。"""

    _clear_bootstrap_env(monkeypatch)

    code, obj, raw = _run_cli(["skills", "preflight", "--workspace-root", str(tmp_path), "--pretty"], capsys)
    assert code == 0
    assert obj["issues"] == []
    assert "\n  " in raw


def test_cli_preflight_dotenv_is_loaded_by_default(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """未设置 --no-dotenv 时，workspace_root/.env 存在则应被加载并记录路径。"""

    (tmp_path / ".env").write_text("REDIS_URL=redis://example\n", encoding="utf-8")
    _clear_bootstrap_env(monkeypatch)
    monkeypatch.delenv("REDIS_URL", raising=False)

    code, obj, _out = _run_cli(["skills", "preflight", "--workspace-root", str(tmp_path)], capsys)
    assert code == 0
    assert obj["stats"]["env_file"] == str((tmp_path / ".env").resolve())


def test_cli_preflight_no_dotenv_disables_loading(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """--no-dotenv 时不加载 .env，env_file == null。"""

    (tmp_path / ".env").write_text("REDIS_URL=redis://example\n", encoding="utf-8")
    _clear_bootstrap_env(monkeypatch)
    monkeypatch.delenv("REDIS_URL", raising=False)

    code, obj, _out = _run_cli(["skills", "preflight", "--workspace-root", str(tmp_path), "--no-dotenv"], capsys)
    assert code == 0
    assert obj["stats"]["env_file"] is None


def test_cli_preflight_warning_only_exit_12(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """仅 warnings 时 exit code 必须为 12。"""

    overlay = tmp_path / "w.yaml"
    _write_yaml(
        overlay,
        {
            "skills": {
                "versioning": {"enabled": False, "unknown_key": 1},
            }
        },
    )
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(
        ["skills", "preflight", "--workspace-root", str(tmp_path), "--config", overlay.name],
        capsys,
    )
    assert code == 12
    assert obj["stats"]["errors_total"] == 0
    assert obj["stats"]["warnings_total"] >= 1
    assert obj["stats"]["issues_total"] == obj["stats"]["warnings_total"]


def test_cli_preflight_error_exit_10(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """存在 errors 时 exit code 必须为 10。"""

    overlay = tmp_path / "e.yaml"
    _write_yaml(
        overlay,
        {
            "skills": {
                "spaces": [{"id": "s", "account": "aa", "domain": "dd", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {}}],
            }
        },
    )
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(
        ["skills", "preflight", "--workspace-root", str(tmp_path), "--config", overlay.name],
        capsys,
    )
    assert code == 10
    assert obj["stats"]["errors_total"] >= 1
    assert obj["stats"]["issues_total"] == obj["stats"]["errors_total"] + obj["stats"]["warnings_total"]


def test_cli_preflight_overlay_order_is_stable(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """--config 的顺序必须保序，并且后者覆盖前者（按 config loader 语义）。"""

    bad = tmp_path / "a.yaml"
    good = tmp_path / "b.yaml"
    _write_yaml(
        bad,
        {
            "skills": {
                "spaces": [{"id": "s", "account": "aa", "domain": "dd", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {}}],
            }
        },
    )
    _write_yaml(
        good,
        {
            "skills": {
                "spaces": [{"id": "s", "account": "aa", "domain": "dd", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills"}}],
            }
        },
    )
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(
        [
            "skills",
            "preflight",
            "--workspace-root",
            str(tmp_path),
            "--config",
            bad.name,
            "--config",
            good.name,
        ],
        capsys,
    )
    assert code == 0
    assert [Path(p).name for p in obj["stats"]["overlay_paths"]] == [bad.name, good.name]


def test_cli_preflight_missing_overlay_is_error_json(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """overlay 不存在时仍尽量输出 JSON（便于脚本消费）。"""

    missing = tmp_path / "missing.yaml"
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(
        ["skills", "preflight", "--workspace-root", str(tmp_path), "--config", missing.name],
        capsys,
    )
    assert code in {2, 10}
    assert isinstance(obj["issues"], list) and obj["issues"]
    assert obj["stats"]["issues_total"] >= 1


def test_cli_preflight_validate_config_alias_works(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """validate-config 作为 preflight 别名应可用（可选但建议）。"""

    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(["skills", "validate-config", "--workspace-root", str(tmp_path)], capsys)
    assert code == 0
    assert obj["issues"] == []


def test_cli_preflight_does_not_leak_dotenv_values(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """stdout JSON 不应包含 .env 中的真实密钥值。"""

    secret = "TOP_SECRET_VALUE_123"
    (tmp_path / ".env").write_text(f"SECRET_X={secret}\n", encoding="utf-8")
    _clear_bootstrap_env(monkeypatch)
    monkeypatch.delenv("SECRET_X", raising=False)

    code, _obj, raw = _run_cli(["skills", "preflight", "--workspace-root", str(tmp_path)], capsys)
    assert code == 0
    assert secret not in raw


# --------------------
# skills scan
# --------------------


def test_cli_scan_default_config_ok(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """scan 默认配置（无 spaces/sources）应输出空 report 且 exit 0。"""

    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(["skills", "scan", "--workspace-root", str(tmp_path)], capsys)
    assert code == 0
    assert obj["scan_id"].startswith("scan_")
    assert obj["skills"] == []
    assert obj["errors"] == []
    assert obj["warnings"] == []
    assert obj["stats"]["skills_total"] == 0


def test_cli_scan_filesystem_skill_has_no_body_markdown(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """filesystem scan 输出必须不包含 skill 正文（body_markdown）。"""

    skills_root = tmp_path / "skills_root"
    _write_skill(skills_root / "s1", name="python_testing", description="d", body="# secret\n")
    overlay = tmp_path / "skills.yaml"
    _write_yaml(
        overlay,
        {
            "skills": {
                "spaces": [{"id": "s", "account": "aa", "domain": "dd", "sources": ["src-fs"]}],
                "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": "skills_root"}}],
            }
        },
    )
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(["skills", "scan", "--workspace-root", str(tmp_path), "--config", overlay.name], capsys)
    assert code == 0
    assert obj["stats"]["skills_total"] == 1
    assert obj["skills"][0]["skill_name"] == "python_testing"
    assert "body_markdown" not in obj["skills"][0]["metadata"]


def test_cli_scan_pretty_output_is_valid_json(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """--pretty 仅影响格式。"""

    _clear_bootstrap_env(monkeypatch)
    code, obj, raw = _run_cli(["skills", "scan", "--workspace-root", str(tmp_path), "--pretty"], capsys)
    assert code == 0
    assert obj["stats"]["skills_total"] == 0
    assert "\n  " in raw


def test_cli_scan_redis_env_present_false_without_dotenv(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """redis source 未设置 env 且不加载 dotenv 时，env_present 应为 False。"""

    overlay = tmp_path / "redis.yaml"
    _write_yaml(
        overlay,
        {
            "skills": {
                "spaces": [{"id": "s", "account": "aa", "domain": "dd", "sources": ["src-redis"]}],
                "sources": [{"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": "skills:"}}],
            }
        },
    )
    monkeypatch.delenv("REDIS_URL", raising=False)
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(
        ["skills", "scan", "--workspace-root", str(tmp_path), "--config", overlay.name, "--no-dotenv"],
        capsys,
    )
    assert code == 11
    assert obj["errors"]
    assert any(it.get("details", {}).get("env_present") is False for it in obj["errors"])


def test_cli_scan_redis_env_present_true_with_dotenv(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """workspace_root/.env 提供 dsn_env 时，env_present 应为 True（即使依赖缺失仍需反映）。"""

    (tmp_path / ".env").write_text("REDIS_URL=redis://example\n", encoding="utf-8")
    overlay = tmp_path / "redis.yaml"
    _write_yaml(
        overlay,
        {
            "skills": {
                "spaces": [{"id": "s", "account": "aa", "domain": "dd", "sources": ["src-redis"]}],
                "sources": [{"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": "skills:"}}],
            }
        },
    )
    monkeypatch.delenv("REDIS_URL", raising=False)
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(["skills", "scan", "--workspace-root", str(tmp_path), "--config", overlay.name], capsys)
    assert code == 11
    assert obj["errors"]
    assert any(it.get("details", {}).get("env_present") is True for it in obj["errors"])


def test_cli_scan_duplicate_skill_name_still_outputs_report(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """duplicate 早失败时仍需输出 ScanReport JSON（exit 11）。"""

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write_skill(root_a / "s1", name="dup_skill", description="a")
    _write_skill(root_b / "s2", name="dup_skill", description="b")
    overlay = tmp_path / "dup.yaml"
    _write_yaml(
        overlay,
        {
            "skills": {
                "spaces": [{"id": "s", "account": "aa", "domain": "dd", "sources": ["src-a", "src-b"]}],
                "sources": [
                    {"id": "src-a", "type": "filesystem", "options": {"root": "a"}},
                    {"id": "src-b", "type": "filesystem", "options": {"root": "b"}},
                ],
            }
        },
    )
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(["skills", "scan", "--workspace-root", str(tmp_path), "--config", overlay.name], capsys)
    assert code == 11
    assert obj["scan_id"].startswith("scan_")
    assert obj["errors"]
    assert any(it.get("code") == "SKILL_DUPLICATE_NAME" for it in obj["errors"])


def test_cli_scan_output_is_strict_json(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """scan 输出必须满足 allow_nan=False 的严格 JSON。"""

    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(["skills", "scan", "--workspace-root", str(tmp_path)], capsys)
    assert code == 0
    json.dumps(obj, allow_nan=False)


def test_cli_scan_missing_overlay_is_error_json(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """overlay 不存在时仍尽量输出 ScanReport JSON（便于脚本消费）。"""

    missing = tmp_path / "missing.yaml"
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(["skills", "scan", "--workspace-root", str(tmp_path), "--config", missing.name], capsys)
    assert code in {2, 11}
    assert obj["scan_id"]
    assert obj["errors"]


def test_cli_scan_redis_env_present_false_with_dotenv_but_disabled(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """存在 .env 但设置 --no-dotenv 时，env_present 仍应为 False。"""

    (tmp_path / ".env").write_text("REDIS_URL=redis://example\n", encoding="utf-8")
    overlay = tmp_path / "redis.yaml"
    _write_yaml(
        overlay,
        {
            "skills": {
                "spaces": [{"id": "s", "account": "aa", "domain": "dd", "sources": ["src-redis"]}],
                "sources": [{"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": "skills:"}}],
            }
        },
    )
    monkeypatch.delenv("REDIS_URL", raising=False)
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(
        ["skills", "scan", "--workspace-root", str(tmp_path), "--config", overlay.name, "--no-dotenv"],
        capsys,
    )
    assert code == 11
    assert obj["errors"]
    assert any(it.get("details", {}).get("env_present") is False for it in obj["errors"])


def test_cli_scan_invalid_workspace_root_is_error_json(tmp_path: Path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """workspace_root 不存在时应稳定失败，并尽量输出 JSON。"""

    missing = tmp_path / "no_such_dir"
    _clear_bootstrap_env(monkeypatch)

    code, obj, _out = _run_cli(["skills", "scan", "--workspace-root", str(missing)], capsys)
    assert code in {2, 11}
    assert obj["scan_id"]
    assert obj["errors"]
