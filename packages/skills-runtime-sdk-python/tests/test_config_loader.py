from __future__ import annotations

from pathlib import Path

from skills_runtime.config.loader import load_config


def _find_sdk_default_yaml(start: Path) -> Path:
    """
    找到 SDK 的默认配置文件（default.yaml）。

    说明：
    - 开源发布场景下，仓库可能不会包含/发布 docs/specs 下的 default.yaml（例如被 .gitignore 排除）。
    - SDK 运行时默认值以 `skills_runtime/assets/default.yaml` 为准（安装包内也会携带）。
    """

    try:
        import skills_runtime.assets as _assets

        p = (Path(_assets.__file__).resolve().parent / "default.yaml").resolve()
        if p.exists():
            return p
    except Exception:
        pass

    # fallback：开发态（未安装）时从 repo 相对路径探测
    start = start.resolve()
    for parent in [start, *start.parents]:
        p2 = parent / "packages" / "skills-runtime-sdk-python" / "src" / "skills_runtime" / "assets" / "default.yaml"
        if p2.exists():
            return p2.resolve()
    raise RuntimeError("default.yaml not found (expected skills_runtime/assets/default.yaml)")


def test_load_config_default_plus_overlay(tmp_path: Path) -> None:
    default_src = _find_sdk_default_yaml(Path(__file__))

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
