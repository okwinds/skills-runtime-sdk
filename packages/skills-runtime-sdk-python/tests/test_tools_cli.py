from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from skills_runtime.cli.main import main


def _parse_last_json(stdout: str) -> Dict[str, Any]:
    """解析 stdout 最后一行 JSON（CLI 约定：stdout 为单个 JSON）。"""

    text = (stdout or "").strip().splitlines()[-1]
    obj = json.loads(text)
    assert isinstance(obj, dict)
    return obj


def test_cli_tools_list_dir_ok(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.txt").write_text("x\n", encoding="utf-8")

    code = main(["tools", "list-dir", "--workspace-root", str(tmp_path), "--dir-path", "d", "--depth", "1"])
    out = capsys.readouterr().out
    payload = _parse_last_json(out)
    assert code == 0
    assert payload.get("tool") == "list_dir"
    assert payload.get("result", {}).get("ok") is True


def test_cli_tools_list_dir_escape_is_permission(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "list-dir", "--workspace-root", str(tmp_path), "--dir-path", "/"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 21
    assert payload["result"]["error_kind"] == "permission"


def test_cli_tools_list_dir_not_found(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "list-dir", "--workspace-root", str(tmp_path), "--dir-path", "missing"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 22
    assert payload["result"]["error_kind"] == "not_found"


def test_cli_tools_grep_files_no_match_ok(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    code = main(["tools", "grep-files", "--workspace-root", str(tmp_path), "--pattern", "world"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload.get("tool") == "grep_files"
    assert payload["result"]["ok"] is True
    assert payload["result"]["data"]["files"] == []


def test_cli_tools_grep_files_invalid_regex_validation(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "grep-files", "--workspace-root", str(tmp_path), "--pattern", "("])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 20
    assert payload["result"]["error_kind"] == "validation"


def test_cli_tools_apply_patch_requires_yes(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    patch = "\n".join(["*** Begin Patch", "*** Add File: a.txt", "+x", "*** End Patch", ""])
    code = main(["tools", "apply-patch", "--workspace-root", str(tmp_path), "--input", patch])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 26
    assert payload["result"]["error_kind"] == "human_required"


def test_cli_tools_apply_patch_ok_add_file(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    patch = "\n".join(["*** Begin Patch", "*** Add File: a.txt", "+x", "*** End Patch", ""])
    code = main(["tools", "apply-patch", "--workspace-root", str(tmp_path), "--yes", "--input", patch])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload.get("tool") == "apply_patch"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x\n"


def test_cli_tools_apply_patch_input_file(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    patch_path = tmp_path / "p.patch"
    patch_path.write_text("\n".join(["*** Begin Patch", "*** Add File: a.txt", "+x", "*** End Patch", ""]), encoding="utf-8")
    code = main(
        [
            "tools",
            "apply-patch",
            "--workspace-root",
            str(tmp_path),
            "--yes",
            "--input-file",
            str(patch_path),
        ]
    )
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload.get("tool") == "apply_patch"
    assert (tmp_path / "a.txt").exists()


def test_cli_tools_apply_patch_escape_is_permission(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    patch = "\n".join(["*** Begin Patch", "*** Add File: ../evil.txt", "+x", "*** End Patch", ""])
    code = main(["tools", "apply-patch", "--workspace-root", str(tmp_path), "--yes", "--input", patch])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 21
    assert payload["result"]["error_kind"] == "permission"


def test_cli_tools_apply_patch_no_overwrite_validation(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "a.txt").write_text("y\n", encoding="utf-8")
    patch = "\n".join(["*** Begin Patch", "*** Add File: a.txt", "+x", "*** End Patch", ""])
    code = main(["tools", "apply-patch", "--workspace-root", str(tmp_path), "--yes", "--input", patch])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 20
    assert payload["result"]["error_kind"] == "validation"


def test_cli_tools_read_file_ok(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("a\nb\n", encoding="utf-8")
    code = main(["tools", "read-file", "--workspace-root", str(tmp_path), "--file-path", "a.txt", "--offset", "2", "--limit", "1"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload.get("tool") == "read_file"
    assert payload["result"]["ok"] is True
    assert "L2: b" in payload["result"]["stdout"]


def test_cli_tools_read_file_not_found(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "read-file", "--workspace-root", str(tmp_path), "--file-path", "missing.txt"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 22
    assert payload["result"]["error_kind"] == "not_found"


def test_cli_tools_read_file_escape_is_permission(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "read-file", "--workspace-root", str(tmp_path), "--file-path", "../evil.txt"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 21
    assert payload["result"]["error_kind"] == "permission"


def test_cli_tools_read_file_indentation_mode_ok(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("\n".join(["def f():", "    x=1", "    y=2", ""]) , encoding="utf-8")
    code = main(
        [
            "tools",
            "read-file",
            "--workspace-root",
            str(tmp_path),
            "--file-path",
            "a.py",
            "--mode",
            "indentation",
            "--offset",
            "2",
            "--anchor-line",
            "2",
            "--max-levels",
            "1",
            "--include-header",
        ]
    )
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload.get("tool") == "read_file"
    assert payload["result"]["ok"] is True
    assert "L1: def f():" in payload["result"]["stdout"]


def test_cli_tools_shell_requires_yes(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "shell", "--workspace-root", str(tmp_path), "--", "python", "-c", "print('hi')"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 26
    assert payload["result"]["error_kind"] == "human_required"


def test_cli_tools_shell_ok(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "shell", "--workspace-root", str(tmp_path), "--yes", "--", "python", "-c", "print('hi')"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload["tool"] == "shell"
    assert payload["result"]["ok"] is True
    assert "hi" in payload["result"]["stdout"]


def test_cli_tools_shell_command_ok(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "shell-command", "--workspace-root", str(tmp_path), "--yes", "--command", "echo hi"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload["tool"] == "shell_command"
    assert payload["result"]["ok"] is True
    assert "hi" in payload["result"]["stdout"]


def test_cli_tools_update_plan_ok(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(
        [
            "tools",
            "update-plan",
            "--workspace-root",
            str(tmp_path),
            "--input",
            json.dumps({"plan": [{"step": "A", "status": "pending"}]}, ensure_ascii=False),
        ]
    )
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload["tool"] == "update_plan"
    assert payload["result"]["ok"] is True


def test_cli_tools_request_user_input_requires_answers_or_provider(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(
        [
            "tools",
            "request-user-input",
            "--workspace-root",
            str(tmp_path),
            "--input",
            json.dumps({"questions": [{"id": "q1", "header": "H", "question": "Q"}]}, ensure_ascii=False),
        ]
    )
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 26
    assert payload["tool"] == "request_user_input"
    assert payload["result"]["error_kind"] == "human_required"


def test_cli_tools_request_user_input_answers_json_ok(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(
        [
            "tools",
            "request-user-input",
            "--workspace-root",
            str(tmp_path),
            "--input",
            json.dumps({"questions": [{"id": "q1", "header": "H", "question": "Q"}]}, ensure_ascii=False),
            "--answers-json",
            json.dumps({"q1": "A"}, ensure_ascii=False),
        ]
    )
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload["tool"] == "request_user_input"
    assert payload["result"]["ok"] is True


def test_cli_tools_view_image_ok(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "a.png").write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X2Z0AAAAASUVORK5CYII="))
    code = main(["tools", "view-image", "--workspace-root", str(tmp_path), "--path", "a.png"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload["tool"] == "view_image"
    assert payload["result"]["ok"] is True
    assert payload["result"]["data"]["mime"] == "image/png"


def test_cli_tools_web_search_disabled(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "web-search", "--workspace-root", str(tmp_path), "--q", "x"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 20
    assert payload["tool"] == "web_search"
    assert payload["result"]["error_kind"] == "validation"


def test_cli_tools_spawn_agent_requires_yes(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "spawn-agent", "--workspace-root", str(tmp_path), "--message", "hi"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 26
    assert payload["tool"] == "spawn_agent"
    assert payload["result"]["error_kind"] == "human_required"


def test_cli_tools_spawn_agent_ok(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["tools", "spawn-agent", "--workspace-root", str(tmp_path), "--yes", "--message", "hi"])
    payload = _parse_last_json(capsys.readouterr().out)
    assert code == 0
    assert payload["tool"] == "spawn_agent"
    assert payload["result"]["ok"] is True
