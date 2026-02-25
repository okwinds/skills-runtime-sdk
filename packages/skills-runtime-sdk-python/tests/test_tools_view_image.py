from __future__ import annotations

import base64
import json
from pathlib import Path

from skills_runtime.tools.builtin.view_image import view_image
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


def _payload(result) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(result.content)


def _mk_ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(workspace_root=tmp_path, run_id="t_view_image", emit_tool_events=False)


_PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X2Z0AAAAASUVORK5CYII="
)


def test_view_image_ok_png(tmp_path: Path) -> None:
    raw = base64.b64decode(_PNG_1X1_BASE64)
    p = tmp_path / "a.png"
    p.write_bytes(raw)
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={"path": "a.png"}), ctx)
    pld = _payload(r)
    assert pld["ok"] is True
    assert pld["data"]["mime"] == "image/png"
    assert pld["data"]["bytes"] == len(raw)
    assert base64.b64decode(pld["data"]["base64"]) == raw


def test_view_image_not_found(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={"path": "missing.png"}), ctx)
    pld = _payload(r)
    assert pld["ok"] is False
    assert pld["error_kind"] == "not_found"


def test_view_image_escape_is_permission(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={"path": "../evil.png"}), ctx)
    pld = _payload(r)
    assert pld["ok"] is False
    assert pld["error_kind"] == "permission"


def test_view_image_path_must_be_file(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    d.mkdir()
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={"path": "dir"}), ctx)
    pld = _payload(r)
    assert pld["ok"] is False
    assert pld["error_kind"] == "validation"


def test_view_image_unknown_extension_has_octet_stream_mime(tmp_path: Path) -> None:
    p = tmp_path / "a.bin"
    p.write_bytes(b"123")
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={"path": "a.bin"}), ctx)
    pld = _payload(r)
    assert pld["ok"] is True
    assert pld["data"]["mime"] == "application/octet-stream"


def test_view_image_jpeg_extension_mime(tmp_path: Path) -> None:
    p = tmp_path / "a.jpg"
    p.write_bytes(b"fakejpg")
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={"path": "a.jpg"}), ctx)
    pld = _payload(r)
    assert pld["ok"] is True
    assert pld["data"]["mime"] == "image/jpeg"


def test_view_image_too_large_is_validation(tmp_path: Path) -> None:
    p = tmp_path / "a.png"
    p.write_bytes(b"x" * (6 * 1024 * 1024))
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={"path": "a.png"}), ctx)
    pld = _payload(r)
    assert pld["ok"] is False
    assert pld["error_kind"] == "validation"


def test_view_image_args_missing_is_validation(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={}), ctx)
    pld = _payload(r)
    assert pld["ok"] is False
    assert pld["error_kind"] == "validation"


def test_view_image_empty_path_is_validation(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={"path": ""}), ctx)
    pld = _payload(r)
    assert pld["ok"] is False
    assert pld["error_kind"] == "validation"


def test_view_image_data_path_is_absolute(tmp_path: Path) -> None:
    raw = base64.b64decode(_PNG_1X1_BASE64)
    p = tmp_path / "a.png"
    p.write_bytes(raw)
    ctx = _mk_ctx(tmp_path)
    r = view_image(ToolCall(call_id="c1", name="view_image", args={"path": "a.png"}), ctx)
    pld = _payload(r)
    assert pld["ok"] is True
    assert str(tmp_path) in pld["data"]["path"]

