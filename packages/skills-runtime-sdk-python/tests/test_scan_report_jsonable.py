from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from skills_runtime.core.errors import FrameworkIssue
from skills_runtime.skills.models import ScanReport, Skill


def _mk_skill(*, path: Path | None, metadata: dict, called: dict[str, int]) -> Skill:
    """构造 Skill fixture（可检测 body_loader 是否被调用）。"""

    def _load() -> str:
        called["count"] = called.get("count", 0) + 1
        raise AssertionError("body_loader must not be called by jsonable view")

    return Skill(
        space_id="space-1",
        source_id="src-1",
        namespace="alice:engineering",
        skill_name="python_testing",
        description="pytest patterns",
        locator="mem://python_testing",
        path=path,
        body_size=123,
        body_loader=_load,
        required_env_vars=["OPENAI_API_KEY"],
        metadata=dict(metadata),
        scope="in-memory",
    )


def _mk_report(*, skills: list[Skill], errors: list[object], warnings: list[object], stats: dict[str, int] | None = None) -> ScanReport:
    """构造 ScanReport fixture。"""

    return ScanReport(
        scan_id="scan_test",
        skills=list(skills),
        errors=list(errors),
        warnings=list(warnings),
        stats=dict(stats or {"spaces_total": 1, "sources_total": 1, "skills_total": len(skills)}),
    )


def test_scan_report_to_jsonable_has_stable_top_level_keys(tmp_path: Path) -> None:
    """to_jsonable() 顶层必须包含 scan_id/skills/errors/warnings/stats。"""

    called: dict[str, int] = {}
    report = _mk_report(
        skills=[_mk_skill(path=tmp_path / "SKILL.md", metadata={"x": 1}, called=called)],
        errors=[],
        warnings=[],
    )

    obj = report.to_jsonable()
    assert set(obj.keys()) >= {"scan_id", "skills", "errors", "warnings", "stats"}
    assert isinstance(obj["scan_id"], str)
    assert isinstance(obj["skills"], list)
    assert isinstance(obj["errors"], list)
    assert isinstance(obj["warnings"], list)
    assert isinstance(obj["stats"], dict)


def test_scan_report_to_jsonable_stats_values_are_int(tmp_path: Path) -> None:
    """stats 的 values 必须为 int（便于上层稳定计数）。"""

    called: dict[str, int] = {}
    report = _mk_report(
        skills=[_mk_skill(path=tmp_path / "SKILL.md", metadata={}, called=called)],
        errors=[],
        warnings=[],
        stats={"spaces_total": 1, "sources_total": 2, "skills_total": 3},
    )

    stats = report.to_jsonable()["stats"]
    assert stats == {"spaces_total": 1, "sources_total": 2, "skills_total": 3}
    assert all(isinstance(v, int) for v in stats.values())


def test_skill_to_metadata_dict_has_required_fields(tmp_path: Path) -> None:
    """SkillJsonable 必须包含 spec 要求的字段集合。"""

    called: dict[str, int] = {}
    skill = _mk_skill(path=tmp_path / "SKILL.md", metadata={"k": "v"}, called=called)
    obj = skill.to_metadata_dict()

    required = {
        "space_id",
        "source_id",
        "namespace",
        "skill_name",
        "description",
        "locator",
        "path",
        "body_size",
        "required_env_vars",
        "metadata",
        "scope",
    }
    assert set(obj.keys()) >= required
    assert isinstance(obj["required_env_vars"], list)
    assert isinstance(obj["metadata"], dict)
    assert isinstance(obj["path"], str)


def test_to_jsonable_does_not_call_body_loader(tmp_path: Path) -> None:
    """jsonable 视图不得触发隐式 I/O（不得调用 body_loader）。"""

    called: dict[str, int] = {}
    report = _mk_report(
        skills=[_mk_skill(path=tmp_path / "SKILL.md", metadata={"k": "v"}, called=called)],
        errors=[],
        warnings=[],
    )
    _ = report.to_jsonable()
    assert called.get("count", 0) == 0


def test_skill_to_metadata_dict_removes_body_markdown(tmp_path: Path) -> None:
    """Skill.metadata 中的 body_markdown 必须被移除，避免泄露正文。"""

    called: dict[str, int] = {}
    skill = _mk_skill(path=tmp_path / "SKILL.md", metadata={"body_markdown": "# secret\n", "x": 1}, called=called)
    obj = skill.to_metadata_dict()
    assert "body_markdown" not in obj["metadata"]
    assert obj["metadata"]["x"] == 1


def test_scan_report_to_jsonable_issues_keep_shape_and_sanitize_details(tmp_path: Path) -> None:
    """errors/warnings 必须保持 code/message/details 结构且 details 可 JSON dumps。"""

    called: dict[str, int] = {}
    issue = FrameworkIssue(
        code="X",
        message="English message.",
        details={
            "path": tmp_path / "x",
            "raw": b"abc",
            "exc": ValueError("bad"),
            1: "k",
            "set": {"a", "b"},
        },
    )
    report = _mk_report(
        skills=[_mk_skill(path=None, metadata={}, called=called)],
        errors=[issue, object()],
        warnings=[{"code": "Y", "message": "m", "details": {"nan": float("nan")}}],
    )

    obj = report.to_jsonable()
    assert obj["errors"][0]["code"] == "X"
    assert obj["errors"][0]["message"] == "English message."
    assert isinstance(obj["errors"][0]["details"], dict)
    assert "path" in obj["errors"][0]["details"]
    assert obj["errors"][0]["details"]["path"] == str(tmp_path / "x")
    assert "raw" in obj["errors"][0]["details"]
    assert obj["errors"][0]["details"]["raw"]["__type__"] == "bytes"
    assert obj["errors"][0]["details"]["raw"]["len"] == 3
    assert isinstance(obj["errors"][0]["details"]["raw"]["sha256"], str) and len(obj["errors"][0]["details"]["raw"]["sha256"]) == 64
    assert obj["errors"][0]["details"]["exc"]["__type__"] == "exception"
    assert obj["errors"][0]["details"]["exc"]["class"] == "ValueError"
    assert obj["errors"][0]["details"]["exc"]["message"] == "bad"
    assert "1" in obj["errors"][0]["details"]
    assert isinstance(obj["errors"][0]["details"]["set"], list)

    # fail-open：非标准 issue 仍必须输出一个可 dumps 的 IssueJsonable
    assert obj["errors"][1]["code"]
    assert obj["errors"][1]["message"]
    assert isinstance(obj["errors"][1]["details"], dict)

    # dict issue 也应被容错为 IssueJsonable
    assert obj["warnings"][0]["code"] == "Y"
    assert obj["warnings"][0]["details"]["nan"] == "NaN"


def test_json_sanitize_handles_infinity_and_nan(tmp_path: Path) -> None:
    """NaN/Inf 必须被降级为字符串，避免 allow_nan=False 失败。"""

    called: dict[str, int] = {}
    issue = FrameworkIssue(code="X", message="m", details={"nan": float("nan"), "p": float("inf"), "n": float("-inf")})
    report = _mk_report(skills=[_mk_skill(path=tmp_path / "SKILL.md", metadata={}, called=called)], errors=[issue], warnings=[])
    obj = report.to_jsonable()
    details = obj["errors"][0]["details"]
    assert details["nan"] == "NaN"
    assert details["p"] == "Infinity"
    assert details["n"] == "-Infinity"


def test_json_sanitize_cycle_is_detected(tmp_path: Path) -> None:
    """循环引用必须被保护，避免递归爆栈。"""

    called: dict[str, int] = {}
    a: list[object] = []
    a.append(a)
    issue = FrameworkIssue(code="X", message="m", details={"cycle": a})
    report = _mk_report(skills=[_mk_skill(path=tmp_path / "SKILL.md", metadata={}, called=called)], errors=[issue], warnings=[])
    obj = report.to_jsonable()
    assert obj["errors"][0]["details"]["cycle"][0] == "<cycle>"


def test_json_sanitize_max_depth_is_enforced(tmp_path: Path) -> None:
    """最大递归深度必须有上限，超限时降级为占位符。"""

    called: dict[str, int] = {}
    deep: object = "leaf"
    for _ in range(20):
        deep = [deep]
    issue = FrameworkIssue(code="X", message="m", details={"deep": deep})
    report = _mk_report(skills=[_mk_skill(path=tmp_path / "SKILL.md", metadata={}, called=called)], errors=[issue], warnings=[])
    obj = report.to_jsonable()
    # 只要在某一层出现占位符即可（不绑定具体层数细节）
    dumped = json.dumps(obj)
    assert "<max_depth_reached>" in dumped


def test_json_dumps_allow_nan_false_does_not_raise(tmp_path: Path) -> None:
    """to_jsonable() 的输出必须满足 allow_nan=False 的严格 JSON。"""

    called: dict[str, int] = {}
    issue = FrameworkIssue(code="X", message="m", details={"nan": math.nan})
    report = _mk_report(skills=[_mk_skill(path=tmp_path / "SKILL.md", metadata={}, called=called)], errors=[issue], warnings=[])
    obj = report.to_jsonable()
    json.dumps(obj, allow_nan=False)
