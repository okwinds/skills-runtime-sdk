from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from skills_runtime.config.loader import load_config_dicts
from skills_runtime.skills.manager import SkillsManager


def _base_config(skills: dict[str, Any]) -> dict[str, Any]:
    """构造最小可校验配置（只覆盖本文件关注的 skills 字段）。"""

    return {
        "config_version": 1,
        "run": {"max_steps": 5},
        "llm": {"base_url": "http://example.test/v1", "api_key_env": "OPENAI_API_KEY"},
        "models": {"planner": "planner-x", "executor": "executor-x"},
        "skills": skills,
    }


def _write_fs_skill(root: Path, *, name: str, description: str) -> Path:
    """写入一个 filesystem skill fixture（最小 frontmatter）。"""

    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f'description: "{description}"',
                "---",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return skill_md


def _scan_signature(report) -> dict[str, Any]:
    """提取 scan 报告的稳定签名，用于跨配置对比（忽略随机 scan_id）。"""

    return {
        "stats": dict(report.stats),
        "errors": [(e.code, e.message, dict(getattr(e, "details", {}) or {})) for e in report.errors],
        "warnings": [(w.code, w.message, dict(getattr(w, "details", {}) or {})) for w in report.warnings],
        "skills": sorted(
            [
                (
                    s.space_id,
                    s.source_id,
                    s.account,
                    s.domain,
                    s.skill_name,
                    s.description,
                    s.locator,
                    str(s.path) if s.path is not None else None,
                )
                for s in report.skills
            ]
        ),
    }


def test_versioning_missing_is_accepted() -> None:
    """缺少 `skills.versioning` 时应可通过校验（兼容旧配置）。"""

    cfg = load_config_dicts([_base_config({"spaces": [], "sources": []})])
    assert cfg.skills is not None


def test_versioning_explicit_is_accepted() -> None:
    """显式提供 `skills.versioning` 时应可通过校验。"""

    cfg = load_config_dicts(
        [
            _base_config(
                {
                    "versioning": {"enabled": False, "strategy": "TODO"},
                    "spaces": [],
                    "sources": [],
                }
            )
        ]
    )
    assert cfg.skills.versioning.strategy == "TODO"


def test_versioning_defaults_when_missing() -> None:
    """当 `skills.versioning` 缺失时应提供安全默认值。"""

    cfg = load_config_dicts([_base_config({})])
    assert cfg.skills.versioning.enabled is False
    assert cfg.skills.versioning.strategy == "TODO"


def test_versioning_defaults_when_empty_object() -> None:
    """当 `skills.versioning: {}` 时仍应回落默认值。"""

    cfg = load_config_dicts([_base_config({"versioning": {}})])
    assert cfg.skills.versioning.enabled is False
    assert cfg.skills.versioning.strategy == "TODO"


def test_versioning_partial_object_keeps_defaults() -> None:
    """当 `skills.versioning` 仅提供部分字段时，缺省字段应使用默认值。"""

    cfg = load_config_dicts([_base_config({"versioning": {"enabled": True}})])
    assert cfg.skills.versioning.enabled is True
    assert cfg.skills.versioning.strategy == "TODO"


def test_versioning_invalid_enabled_type_raises() -> None:
    """当 `enabled` 类型非法（如字符串）时应抛出 ValidationError。"""

    with pytest.raises(ValidationError):
        load_config_dicts([_base_config({"versioning": {"enabled": "yes"}})])


def test_versioning_invalid_root_type_raises() -> None:
    """当 `skills.versioning` 根节点类型非法（如字符串）时应抛出 ValidationError。"""

    with pytest.raises(ValidationError):
        load_config_dicts([_base_config({"versioning": "str"})])


def test_versioning_rejects_extra_fields() -> None:
    """`skills.versioning` 下未知字段必须被严格拒绝（fail-fast）。"""

    with pytest.raises(ValidationError):
        load_config_dicts(
            [
                _base_config(
                    {
                        "versioning": {"enabled": False, "strategy": "TODO", "rollout": {"pct": 10}},
                        "spaces": [],
                        "sources": [],
                    }
                )
            ]
        )


def test_skills_manager_scan_is_unchanged_with_versioning(tmp_path: Path) -> None:
    """SkillsManager.scan() 不应因占位的 versioning 配置发生行为变化。"""

    fs_root = tmp_path / "skills"
    _write_fs_skill(fs_root, name="python_testing", description="pytest patterns")

    base_skills_cfg: Dict[str, Any] = {
        "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
        "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
    }

    cfg_without = load_config_dicts([_base_config(dict(base_skills_cfg))]).skills
    cfg_with = load_config_dicts(
        [
            _base_config(
                {
                    **base_skills_cfg,
                    "versioning": {"enabled": True, "strategy": "TODO"},
                }
            )
        ]
    ).skills

    mgr_without = SkillsManager(workspace_root=tmp_path, skills_config=cfg_without)
    mgr_with = SkillsManager(workspace_root=tmp_path, skills_config=cfg_with)

    report_without = mgr_without.scan()
    report_with = mgr_with.scan()

    assert _scan_signature(report_with) == _scan_signature(report_without)


def test_versioning_can_be_enabled_without_affecting_scan(tmp_path: Path) -> None:
    """当 `enabled=True` 时，scan 行为仍应保持一致（占位不生效）。"""

    fs_root = tmp_path / "skills"
    _write_fs_skill(fs_root, name="python_testing", description="pytest patterns")

    skills_cfg = {
        "spaces": [{"id": "space-eng", "account": "alice", "domain": "engineering", "sources": ["src-fs"]}],
        "sources": [{"id": "src-fs", "type": "filesystem", "options": {"root": str(fs_root)}}],
        "versioning": {"enabled": True, "strategy": "TODO"},
    }
    cfg = load_config_dicts([_base_config(skills_cfg)]).skills
    mgr = SkillsManager(workspace_root=tmp_path, skills_config=cfg)

    report = mgr.scan()
    assert report.stats["skills_total"] == 1
