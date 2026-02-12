from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from agent_sdk.config.loader import AgentSdkSkillsConfig, load_config_dicts
from agent_sdk.core.errors import FrameworkError
from agent_sdk.skills.manager import SkillsManager
from agent_sdk.skills.mentions import extract_skill_mentions


def _base_config(skills: dict[str, Any]) -> dict[str, Any]:
    """构造最小可校验配置（只覆盖本文件关注的 skills 字段）。"""

    return {
        "config_version": 1,
        "run": {"max_steps": 5},
        "llm": {"base_url": "http://example.test/v1", "api_key_env": "OPENAI_API_KEY"},
        "models": {"planner": "planner-x", "executor": "executor-x"},
        "skills": skills,
    }


def _build_skills_config(skills: dict[str, Any]) -> AgentSdkSkillsConfig:
    """从 dict 构造 `AgentSdkSkillsConfig`，避免直接依赖内部默认值。"""

    return load_config_dicts([_base_config(skills)]).skills


def _write_fs_skill(
    root: Path,
    *,
    name: str,
    description: str,
    body: str = "# Body\n",
    locator_hint: str | None = None,
) -> Path:
    """写入一个 filesystem skill fixture。"""

    skill_dir = root / (locator_hint or name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f'description: "{description}"',
                "---",
                body.rstrip("\n"),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return skill_md


def _mk_manager(
    tmp_path: Path,
    *,
    skills: dict[str, Any],
    in_memory_registry: dict[str, list[dict[str, Any]]] | None = None,
) -> SkillsManager:
    """构建 SkillsManager（V2 配置驱动）。"""

    return SkillsManager(
        workspace_root=tmp_path,
        skills_config=_build_skills_config(skills),
        in_memory_registry=in_memory_registry or {},
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$[alice:engineering].python_testing", [("alice", "engineering", "python_testing")]),
        ("$[agentops:platform].redis_cache", [("agentops", "platform", "redis_cache")]),
        ("$[alice:engineering].python_testing then do something", [("alice", "engineering", "python_testing")]),
        ("ps -p $PPID -o command=", []),
        ("literal \\$python_testing is not a mention", []),
        ("$python_testing", []),
        ("$[a:engineering].python_testing", []),
        ("$[alice:e].python_testing", []),
        ("$[alice:engineering].a", []),
        ("$[alice].python_testing", []),
        ("$[alice:engineering]python_testing", []),
        ("$[alice:engineering].python testing", [("alice", "engineering", "python")]),
        ("$[alice:engine:core].python_testing", []),
        ("$[alice:engineering].python]testing", []),
        ("$[alice:engineering].python:testing", [("alice", "engineering", "python")]),
        ("$[AlicE:engineering].python_testing", []),
        ("text $[alice:engineering].python_testing text", [("alice", "engineering", "python_testing")]),
        (
            "$[alice:engineering].python_testing,$[alice:engineering].redis_cache",
            [("alice", "engineering", "python_testing"), ("alice", "engineering", "redis_cache")],
        ),
        ("env $PATH should be ignored", []),
        ("link [$python_testing](./skills/python_testing)", []),
    ],
)
def test_skills_v2_mention_conformance(
    text: str,
    expected: list[tuple[str, str, str]],
) -> None:
    """V2 mention 契约：自由文本中仅提取合法 `$[account:domain].skill_name`。"""

    mentions = extract_skill_mentions(text)
    got = [(m.account, m.domain, m.skill_name) for m in mentions]
    assert got == expected


@pytest.mark.parametrize(
    "case_id",
    [
        "K-001",  # unknown skill
        "K-002",  # space not configured
        "K-003",  # space disabled
        "K-004",  # source unavailable
        "K-005",  # mixed mentions, one unknown
        "K-006",  # all unknown
        "K-007",  # skills config missing
        "K-008",  # domain mismatch
        "K-009",  # account mismatch
        "K-010",  # unknown structure
    ],
)
def test_skills_v2_unknown_and_space_errors(case_id: str, tmp_path: Path) -> None:
    """Unknown/未配置类错误：必须报框架错误且包含英文 code/message/details。"""

    fs_root = tmp_path / "skills"
    _write_fs_skill(fs_root, name="python_testing", description="pytest patterns")

    skills_cfg: dict[str, Any]
    mention: str
    expected_code = "SKILL_UNKNOWN"

    if case_id == "K-001":
        mention = "$[alice:engineering].not_exists"
        skills_cfg = {
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        }
    elif case_id == "K-002":
        mention = "$[alice:ops].python_testing"
        expected_code = "SKILL_SPACE_NOT_CONFIGURED"
        skills_cfg = {
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        }
    elif case_id == "K-003":
        mention = "$[alice:engineering].python_testing"
        expected_code = "SKILL_SPACE_NOT_CONFIGURED"
        skills_cfg = {
            "spaces": [
                {
                    "id": "space-eng",
                    "account": "alice",
                    "domain": "engineering",
                    "sources": ["src-fs"],
                    "enabled": False,
                }
            ],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        }
    elif case_id == "K-004":
        mention = "$[alice:engineering].python_testing"
        expected_code = "SKILL_SCAN_SOURCE_UNAVAILABLE"
        skills_cfg = {
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-redis"]}],
            "sources": [{"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": "skills:"}}],
        }
    elif case_id == "K-005":
        mention = "$[alice:engineering].python_testing,$[alice:engineering].unknown_one"
        skills_cfg = {
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        }
    elif case_id == "K-006":
        mention = "$[alice:engineering].unknown_a,$[alice:engineering].unknown_b"
        skills_cfg = {
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        }
    elif case_id == "K-007":
        mention = "$[alice:engineering].python_testing"
        expected_code = "SKILL_SPACE_NOT_CONFIGURED"
        skills_cfg = {}
    elif case_id == "K-008":
        mention = "$[alice:platform].python_testing"
        expected_code = "SKILL_SPACE_NOT_CONFIGURED"
        skills_cfg = {
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        }
    elif case_id == "K-009":
        mention = "$[bob:engineering].python_testing"
        expected_code = "SKILL_SPACE_NOT_CONFIGURED"
        skills_cfg = {
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        }
    else:
        mention = "$[alice:engineering].still_unknown"
        skills_cfg = {
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
            "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        }

    mgr = _mk_manager(tmp_path, skills=skills_cfg)
    mgr.scan()
    with pytest.raises(FrameworkError) as exc_info:
        mgr.resolve_mentions(mention)

    err = exc_info.value
    assert err.code == expected_code
    assert isinstance(err.message, str) and err.message
    assert isinstance(err.details, dict)
    if case_id == "K-010":
        assert err.details.get("mention") == mention


@pytest.mark.parametrize(
    "case_id,payload_builder",
    [
        ("U-001", lambda root: [
            ("space-a", "src-fs-a", "filesystem", {"root": str(root / "fs-a")}, ["dup_name", "dup_name"]),
        ]),
        ("U-002", lambda root: [
            ("space-a", "src-fs-a", "filesystem", {"root": str(root / "fs-a")}, ["dup_name"]),
            ("space-a", "src-fs-b", "filesystem", {"root": str(root / "fs-b")}, ["dup_name"]),
        ]),
        ("U-003", lambda root: [
            ("space-a", "src-fs-a", "filesystem", {"root": str(root / "fs-a")}, ["dup_name"]),
            ("space-b", "src-fs-b", "filesystem", {"root": str(root / "fs-b")}, ["dup_name"]),
        ]),
        ("U-004", lambda root: [
            ("space-a", "src-fs", "filesystem", {"root": str(root / "fs-a")}, ["dup_name"]),
            ("space-a", "src-mem", "in-memory", {"namespace": "ns-u4"}, ["dup_name"]),
        ]),
        ("U-005", lambda root: [
            ("space-a", "src-mem-a", "in-memory", {"namespace": "ns-u5a"}, ["dup_name"]),
            ("space-a", "src-mem-b", "in-memory", {"namespace": "ns-u5b"}, ["dup_name"]),
        ]),
        ("U-006", lambda root: [
            ("space-a", "src-fs", "filesystem", {"root": str(root / "fs-a")}, ["dup_name"]),
            ("space-b", "src-mem", "in-memory", {"namespace": "ns-u6"}, ["dup_name"]),
        ]),
        ("U-007", lambda root: [
            ("space-a", "src-fs", "filesystem", {"root": str(root / "fs-a")}, ["dup_name"]),
            ("space-a", "src-mem-a", "in-memory", {"namespace": "ns-u7a"}, ["dup_name"]),
            ("space-b", "src-mem-b", "in-memory", {"namespace": "ns-u7b"}, ["dup_name"]),
        ]),
        ("U-008", lambda root: [
            ("space-a", "src-fs", "filesystem", {"root": str(root / "fs-a")}, ["dup_name::desc_a"]),
            ("space-b", "src-fs-b", "filesystem", {"root": str(root / "fs-b")}, ["dup_name::desc_b"]),
        ]),
        ("U-009", lambda root: [
            ("space-a", "src-fs", "filesystem", {"root": str(root / "fs-a")}, ["dup_name::body_a"]),
            ("space-b", "src-fs-b", "filesystem", {"root": str(root / "fs-b")}, ["dup_name::body_b"]),
        ]),
        ("U-010", lambda root: [
            ("space-a", "src-fs-a", "filesystem", {"root": str(root / "fs-a")}, ["dup_name"]),
            ("space-b", "src-mem", "in-memory", {"namespace": "ns-u10"}, ["dup_name"]),
        ]),
    ],
)
def test_skills_v2_duplicate_name_early_fail(
    case_id: str,
    payload_builder: Callable[[Path], list[tuple[str, str, str, dict[str, Any], list[str]]]],
    tmp_path: Path,
) -> None:
    """重复 skill_name 必须在 scan 阶段早失败并返回 conflicts 明细。"""

    data_root = tmp_path / "dup"
    rows = payload_builder(data_root)

    sources: list[dict[str, Any]] = []
    spaces: dict[str, dict[str, Any]] = {}
    in_memory_registry: dict[str, list[dict[str, Any]]] = {}

    for space_id, source_id, source_type, options, names in rows:
        sources.append({"id": source_id, "type": source_type, "options": options})
        if space_id not in spaces:
            account = "alice" if space_id.endswith("a") else "bob"
            spaces[space_id] = {
                "id": space_id,
                "account": account,
                "domain": "engineering",
                "sources": [],
            }
        spaces[space_id]["sources"].append(source_id)

        if source_type == "filesystem":
            fs_root = Path(options["root"])
            for idx, raw in enumerate(names):
                if "::" in raw:
                    skill_name, marker = raw.split("::", 1)
                    _write_fs_skill(
                        fs_root,
                        name=skill_name,
                        description=marker,
                        body=f"{marker}\n",
                        locator_hint=f"{skill_name}_{marker}_{idx}",
                    )
                else:
                    _write_fs_skill(
                        fs_root,
                        name=raw,
                        description=f"desc-{case_id}",
                        locator_hint=f"{raw}_{idx}",
                    )
        elif source_type == "in-memory":
            namespace = str(options["namespace"])
            bucket = in_memory_registry.setdefault(namespace, [])
            for raw in names:
                if "::" in raw:
                    skill_name, marker = raw.split("::", 1)
                    bucket.append(
                        {
                            "skill_name": skill_name,
                            "description": f"desc-{marker}",
                            "body": f"# {marker}\n",
                            "locator": f"mem://{namespace}/{skill_name}/{marker}",
                        }
                    )
                else:
                    bucket.append(
                        {
                            "skill_name": raw,
                            "description": f"desc-{case_id}",
                            "body": f"# {case_id}\n",
                            "locator": f"mem://{namespace}/{raw}",
                        }
                    )

    mgr = _mk_manager(
        tmp_path,
        skills={"spaces": list(spaces.values()), "sources": sources},
        in_memory_registry=in_memory_registry,
    )

    with pytest.raises(FrameworkError) as exc_info:
        mgr.scan()

    err = exc_info.value
    assert err.code == "SKILL_DUPLICATE_NAME"
    assert "Duplicate" in err.message
    assert err.details.get("skill_name") == "dup_name"
    conflicts = err.details.get("conflicts")
    assert isinstance(conflicts, list) and len(conflicts) >= 2
    assert all(isinstance(x, dict) for x in conflicts)
    assert all("space_id" in x and "source_id" in x and "locator" in x for x in conflicts)


@pytest.mark.parametrize(
    "max_bytes,body_size,expect_code",
    [
        (None, 1024 * 1024, None),
        (1024, 1024, None),
        (1024, 1025, "SKILL_BODY_TOO_LARGE"),
        (8, 9, "SKILL_BODY_TOO_LARGE"),
        (64, 10, None),
        (2048, 2049, "SKILL_BODY_TOO_LARGE"),
        (4096, 4096, None),
        (4096, 5000, "SKILL_BODY_TOO_LARGE"),
        (1, 1, None),
        (1, 2, "SKILL_BODY_TOO_LARGE"),
    ],
)
def test_skills_v2_lazy_load_and_max_bytes(
    tmp_path: Path,
    max_bytes: int | None,
    body_size: int,
    expect_code: str | None,
) -> None:
    """lazy-load + max_bytes：scan 不读正文，inject 才读，超限报错。"""

    reads = {"count": 0}

    def _body_loader() -> str:
        reads["count"] += 1
        return "x" * body_size

    mention = "$[alice:engineering].python_testing"
    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-mem"]}],
            "sources": [{"id": "src-mem", "type": "in-memory", "options": {"namespace": "ns-lazy"}}],
            "injection": {"max_bytes": max_bytes},
        },
        in_memory_registry={
            "ns-lazy": [
                {
                    "skill_name": "python_testing",
                    "description": "pytest patterns",
                    "body_loader": _body_loader,
                    "locator": "mem://ns-lazy/python_testing",
                    "body_size": body_size,
                }
            ]
        },
    )

    report = mgr.scan()
    assert report.stats["skills_total"] == 1
    assert reads["count"] == 0

    resolved = mgr.resolve_mentions(mention)
    assert len(resolved) == 1
    assert reads["count"] == 0

    skill, mention_obj = resolved[0]
    if expect_code is None:
        rendered = mgr.render_injected_skill(skill, source="mention", mention_text=mention_obj.mention_text)
        assert "python_testing" in rendered
        assert reads["count"] == 1
    else:
        with pytest.raises(FrameworkError) as exc_info:
            mgr.render_injected_skill(skill, source="mention", mention_text=mention_obj.mention_text)
        assert exc_info.value.code == expect_code
        assert exc_info.value.details.get("limit_bytes") == max_bytes
        assert exc_info.value.details.get("actual_bytes") == body_size
        assert reads["count"] == 1


def test_skills_v2_body_read_failed(tmp_path: Path) -> None:
    """正文读取失败必须返回 `SKILL_BODY_READ_FAILED`。"""

    def _boom() -> str:
        raise OSError("disk read failed")

    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-mem"]}],
            "sources": [{"id": "src-mem", "type": "in-memory", "options": {"namespace": "ns-read-fail"}}],
        },
        in_memory_registry={
            "ns-read-fail": [
                {
                    "skill_name": "python_testing",
                    "description": "pytest patterns",
                    "body_loader": _boom,
                    "locator": "mem://ns-read-fail/python_testing",
                }
            ]
        },
    )
    mgr.scan()
    skill, mention_obj = mgr.resolve_mentions("$[alice:engineering].python_testing")[0]

    with pytest.raises(FrameworkError) as exc_info:
        mgr.render_injected_skill(skill, source="mention", mention_text=mention_obj.mention_text)
    assert exc_info.value.code == "SKILL_BODY_READ_FAILED"
    assert "locator" in exc_info.value.details


def test_skills_v2_scan_report_shape_and_types(tmp_path: Path) -> None:
    """ScanReport 结构必须包含 scan_id/stats/errors/warnings，并保持稳定字段类型。"""

    fs_root = tmp_path / "skills"
    _write_fs_skill(fs_root, name="python_testing", description="pytest patterns")
    mgr = _mk_manager(
        tmp_path,
        skills={
            "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs", "src-redis"]}],
            "sources": [
                {"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}},
                {"id": "src-redis", "type": "redis", "options": {"dsn_env": "REDIS_URL", "key_prefix": "skills:"}},
            ],
        },
    )

    report = mgr.scan()

    checks: list[tuple[str, Callable[[Any], None]]] = [
        ("R-001", lambda r: isinstance(r.scan_id, str) and bool(r.scan_id.strip())),
        ("R-002", lambda r: r.stats["spaces_total"] == 1),
        ("R-003", lambda r: r.stats["sources_total"] == 2),
        ("R-004", lambda r: r.stats["skills_total"] == 1),
        ("R-005", lambda r: all(hasattr(e, "code") and hasattr(e, "message") and hasattr(e, "details") for e in r.errors)),
        ("R-006", lambda r: all(hasattr(w, "code") and hasattr(w, "message") and hasattr(w, "details") for w in r.warnings)),
        ("R-007", lambda r: isinstance(r.skills, list)),
        ("R-008", lambda r: any(e.code == "SKILL_SCAN_SOURCE_UNAVAILABLE" for e in r.errors)),
        ("R-009", lambda r: all(isinstance(e.message, str) and e.message for e in r.errors)),
        ("R-010", lambda r: all(not any("\u4e00" <= ch <= "\u9fff" for ch in e.message) for e in r.errors)),
    ]

    for case_id, checker in checks:
        assert checker(report), case_id
