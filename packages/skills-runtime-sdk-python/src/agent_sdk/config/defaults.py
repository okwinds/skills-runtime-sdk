"""
默认配置（best practice）加载器。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/configuration.md`
- `docs/specs/skills-runtime-sdk/docs/config-paths.md`

设计目标：
- SDK 作为通用框架被引用时，不依赖 repo 相对路径即可运行
- 默认配置通过 `importlib.resources` 随 package 分发
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


def load_default_config_dict() -> Dict[str, Any]:
    """
    读取 SDK 内置默认配置（YAML）并返回 dict。

    返回：
    - dict：用于与 overlays 做深度合并（overlay 语义由 `agent_sdk.config.loader` 定义）

    异常：
    - RuntimeError：读取失败或内容不是 mapping(dict)
    """

    text: str | None = None
    try:
        from importlib.resources import files

        text = files("agent_sdk.assets").joinpath("default.yaml").read_text(encoding="utf-8")
    except Exception:
        # 开发态兼容：当未按 package 形式安装时，允许在 repo 内探测对照样例。
        # 约束：该 fallback 仅用于开发/测试，不应成为“产品运行时依赖”。
        for parent in Path(__file__).resolve().parents:
            candidate = parent / "docs" / "specs" / "skills-runtime-sdk" / "config" / "default.yaml"
            if candidate.exists():
                text = candidate.read_text(encoding="utf-8")
                break
        if text is None:  # pragma: no cover
            raise RuntimeError("failed to load embedded default config (assets not available, repo fallback not found)")

    obj = yaml.safe_load(text) or {}
    if not isinstance(obj, dict):
        raise RuntimeError("embedded default config root must be a mapping(dict)")
    return obj
