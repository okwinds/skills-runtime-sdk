from __future__ import annotations

from pathlib import Path

import pytest

from skills_runtime.skills.manager import SkillsManager
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


def _write_skill_bundle(
    bundle_root: Path,
    *,
    name: str = "python_testing",
    description: str = "d",
    extra_frontmatter_lines: list[str] | None = None,
    body: str = "body\n",
) -> Path:
    """写入一个最小 filesystem skill bundle，并返回 `SKILL.md` 路径。"""

    bundle_root.mkdir(parents=True, exist_ok=True)
    skill_md = bundle_root / "SKILL.md"
    fm = ["---", f"name: {name}", f'description: "{description}"']
    fm.extend(list(extra_frontmatter_lines or []))
    fm.append("---")
    skill_md.write_text("\n".join([*fm, body.rstrip("\n"), ""]), encoding="utf-8")
    return skill_md


def _mk_manager(*, workspace_root: Path, skills_root: Path, skills_config_extra: dict | None = None) -> SkillsManager:
    """创建一个仅包含 filesystem source 的 SkillsManager（便于 actions/ref-read 单测）。"""

    cfg: dict = {
        "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
        "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(skills_root)}}],
    }
    if skills_config_extra:
        cfg.update(skills_config_extra)
    mgr = SkillsManager(workspace_root=workspace_root, skills_config=cfg)
    mgr.scan()
    return mgr


def _mk_ctx(*, workspace_root: Path, skills_manager: SkillsManager) -> ToolExecutionContext:
    """构造 ToolExecutionContext（ref_read 不需要 executor）。"""

    return ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="run_test",
        wal=None,
        executor=None,
        human_io=None,
        env={},
        cancel_checker=None,
        redaction_values=[],
        default_timeout_ms=123,
        max_file_bytes=10,
        sandbox_policy_default="none",
        sandbox_adapter=None,
        emit_tool_events=False,
        event_sink=None,
        skills_manager=skills_manager,
    )


def _call_ref_read(*, ref_path: str, max_bytes: int | None = None) -> ToolCall:
    """构造 skill_ref_read ToolCall。"""

    args: dict = {"skill_mention": "$[alice:engineering].python_testing", "ref_path": ref_path}
    if max_bytes is not None:
        args["max_bytes"] = max_bytes
    return ToolCall(call_id="c1", name="skill_ref_read", args=args)


def _assert_framework_error_payload(result, *, code: str) -> None:  # type: ignore[no-untyped-def]
    """断言 ToolResult 以框架错误结构返回（英文 code/message/details）。"""

    assert result.ok is False
    assert isinstance(result.details, dict)
    data = (result.details or {}).get("data") or {}
    assert isinstance(data, dict)
    err = data.get("error") or {}
    assert isinstance(err, dict)
    assert err.get("code") == code
    assert isinstance(err.get("message"), str) and err.get("message")
    assert isinstance(err.get("details"), dict)


def test_skill_ref_read_disabled_returns_permission(tmp_path: Path) -> None:
    """references 默认关闭：必须直接失败（permission），不读取任何文件。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(bundle)
    (bundle / "references").mkdir(parents=True, exist_ok=True)
    (bundle / "references" / "a.txt").write_text("x", encoding="utf-8")

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"references": {"enabled": False}})
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    result = skill_ref_read(_call_ref_read(ref_path="references/a.txt"), ctx)
    assert result.error_kind == "permission"
    _assert_framework_error_payload(result, code="SKILL_REFERENCES_DISABLED")


def test_skill_ref_read_invalid_mention_returns_validation(tmp_path: Path) -> None:
    """skill_mention 非法必须失败（validation）。"""

    skills_root = tmp_path / "skills_root"
    _write_skill_bundle(skills_root / "python_testing")
    mgr = _mk_manager(
        workspace_root=tmp_path,
        skills_root=skills_root,
        skills_config_extra={"references": {"enabled": True}},
    )
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    call = ToolCall(call_id="c1", name="skill_ref_read", args={"skill_mention": "$[bad", "ref_path": "references/a.txt"})
    result = skill_ref_read(call, ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_MENTION_FORMAT_INVALID")


def test_skill_ref_read_unknown_skill_returns_not_found(tmp_path: Path) -> None:
    """skill 不存在必须失败（not_found）。"""

    skills_root = tmp_path / "skills_root"
    _write_skill_bundle(skills_root / "python_testing")
    mgr = _mk_manager(
        workspace_root=tmp_path,
        skills_root=skills_root,
        skills_config_extra={"references": {"enabled": True}},
    )
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    call = ToolCall(
        call_id="c1",
        name="skill_ref_read",
        args={"skill_mention": "$[alice:engineering].does_not_exist", "ref_path": "references/a.txt"},
    )
    result = skill_ref_read(call, ctx)
    assert result.ok is False
    assert result.error_kind == "not_found"
    _assert_framework_error_payload(result, code="SKILL_UNKNOWN")


def test_skill_ref_read_rejects_unknown_args(tmp_path: Path) -> None:
    """skill_ref_read args 出现未知字段必须失败（validation）。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(bundle)
    (bundle / "references").mkdir(parents=True, exist_ok=True)
    (bundle / "references" / "a.txt").write_text("x", encoding="utf-8")

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"references": {"enabled": True}})
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    call = ToolCall(
        call_id="c1",
        name="skill_ref_read",
        args={"skill_mention": "$[alice:engineering].python_testing", "ref_path": "references/a.txt", "x": "1"},
    )
    result = skill_ref_read(call, ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_REF_PATH_INVALID")


def test_skill_ref_read_source_unsupported_for_in_memory(tmp_path: Path) -> None:
    """非 filesystem source（in-memory）必须失败（validation），并返回 ..._SOURCE_UNSUPPORTED。"""

    mgr = SkillsManager(
        workspace_root=tmp_path,
        skills_config={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-mem"]}],
            "sources": [{"id": "src-mem", "type": "in-memory", "options": {"namespace": "ns"}}],
            "references": {"enabled": True},
        },
        in_memory_registry={"ns": [{"skill_name": "python_testing", "description": "d", "body": "body"}]},
    )
    mgr.scan()
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    result = skill_ref_read(_call_ref_read(ref_path="references/a.txt"), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    _assert_framework_error_payload(result, code="SKILL_REF_SOURCE_UNSUPPORTED")


@pytest.mark.parametrize(
    "ref_path",
    [
        "/etc/passwd",
        "../secrets.txt",
        "SKILL.md",
        "references/../SKILL.md",
        "assets/a.txt",
    ],
)
def test_skill_ref_read_path_invalid_is_permission(tmp_path: Path, ref_path: str) -> None:
    """ref_path 非法必须拒绝（permission）。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(bundle)
    (bundle / "references").mkdir(parents=True, exist_ok=True)
    (bundle / "references" / "a.txt").write_text("x", encoding="utf-8")

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"references": {"enabled": True}})
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    result = skill_ref_read(_call_ref_read(ref_path=ref_path), ctx)
    assert result.ok is False
    assert result.error_kind == "permission"
    _assert_framework_error_payload(result, code="SKILL_REF_PATH_INVALID")


def test_skill_ref_read_assets_requires_allow_assets(tmp_path: Path) -> None:
    """assets/ 默认不可读；allow_assets=true 时才允许。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(bundle)
    (bundle / "assets").mkdir(parents=True, exist_ok=True)
    (bundle / "assets" / "x.txt").write_text("ok", encoding="utf-8")

    mgr = _mk_manager(
        workspace_root=tmp_path,
        skills_root=skills_root,
        skills_config_extra={"references": {"enabled": True, "allow_assets": False}},
    )
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    result = skill_ref_read(_call_ref_read(ref_path="assets/x.txt"), ctx)
    assert result.ok is False
    assert result.error_kind == "permission"
    _assert_framework_error_payload(result, code="SKILL_REF_PATH_INVALID")

    mgr2 = _mk_manager(
        workspace_root=tmp_path,
        skills_root=skills_root,
        skills_config_extra={"references": {"enabled": True, "allow_assets": True}},
    )
    ctx2 = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr2)
    ok = skill_ref_read(_call_ref_read(ref_path="assets/x.txt"), ctx2)
    assert ok.ok is True
    assert ok.details and ok.details["stdout"] == "ok"


def test_skill_ref_read_path_escape_via_symlink_is_rejected(tmp_path: Path) -> None:
    """references/ 内部符号链接指向 bundle 外必须拒绝（SKILL_REF_PATH_ESCAPE）。"""

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(bundle)
    ref_dir = bundle / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "link.txt").symlink_to(outside)

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"references": {"enabled": True}})
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    result = skill_ref_read(_call_ref_read(ref_path="references/link.txt"), ctx)
    assert result.ok is False
    assert result.error_kind == "permission"
    _assert_framework_error_payload(result, code="SKILL_REF_PATH_ESCAPE")


def test_skill_ref_read_not_found_returns_not_found(tmp_path: Path) -> None:
    """合法路径但文件不存在：not_found。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(bundle)
    (bundle / "references").mkdir(parents=True, exist_ok=True)

    mgr = _mk_manager(workspace_root=tmp_path, skills_root=skills_root, skills_config_extra={"references": {"enabled": True}})
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    result = skill_ref_read(_call_ref_read(ref_path="references/missing.txt"), ctx)
    assert result.ok is False
    assert result.error_kind == "not_found"
    _assert_framework_error_payload(result, code="SKILL_REF_NOT_FOUND")


def test_skill_ref_read_truncates_and_marks_truncated(tmp_path: Path) -> None:
    """超过 max_bytes 必须截断，并设置 truncated=true。"""

    skills_root = tmp_path / "skills_root"
    bundle = skills_root / "python_testing"
    _write_skill_bundle(bundle)
    (bundle / "references").mkdir(parents=True, exist_ok=True)
    (bundle / "references" / "big.txt").write_text("a" * 1000, encoding="utf-8")

    mgr = _mk_manager(
        workspace_root=tmp_path,
        skills_root=skills_root,
        skills_config_extra={"references": {"enabled": True, "default_max_bytes": 100}},
    )
    ctx = _mk_ctx(workspace_root=tmp_path, skills_manager=mgr)

    from skills_runtime.tools.builtin.skill_ref_read import skill_ref_read

    result = skill_ref_read(_call_ref_read(ref_path="references/big.txt", max_bytes=120), ctx)
    assert result.ok is True
    assert result.details is not None
    assert result.details["truncated"] is True
    assert "<truncated>" in result.details["stdout"]
