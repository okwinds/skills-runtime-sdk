from __future__ import annotations

from pathlib import Path

from agent_sdk.config.loader import load_config


def _find_repo_root(start: Path) -> Path:
    """从给定路径向上探测包含 `docs/specs/skills-runtime-sdk/config/default.yaml` 的项目根目录。"""

    start = start.resolve()
    for parent in [start, *start.parents]:
        if (parent / "docs" / "specs" / "skills-runtime-sdk" / "config" / "default.yaml").exists():
            return parent
    raise RuntimeError("repo root not found (missing docs/specs/skills-runtime-sdk/config/default.yaml)")


def test_load_config_default_plus_overlay(tmp_path: Path) -> None:
    repo_root = _find_repo_root(Path(__file__))
    default_src = repo_root / "docs" / "specs" / "skills-runtime-sdk" / "config" / "default.yaml"

    default_path = tmp_path / "default.yaml"
    default_path.write_text(default_src.read_text(encoding="utf-8"), encoding="utf-8")

    overlay_path = tmp_path / "overlay.yaml"
    overlay_path.write_text(
        "\n".join(
            [
                "config_version: 1",
                "run:",
                "  max_steps: 7",
                "llm:",
                '  base_url: "http://example.test/v1"',
                "models:",
                '  planner: "planner-x"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cfg = load_config([default_path, overlay_path])

    assert cfg.config_version == 1
    assert cfg.run.max_steps == 7
    assert cfg.llm.base_url == "http://example.test/v1"
    assert cfg.models.planner == "planner-x"
