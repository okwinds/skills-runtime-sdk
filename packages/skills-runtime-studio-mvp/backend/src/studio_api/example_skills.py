from __future__ import annotations

import shutil
from pathlib import Path
from typing import List


_BUNDLE_DIR = Path(__file__).resolve().parent / "example_skills_bundle"


def ensure_example_skills_installed(*, workspace_root: Path) -> List[Path]:
    """
    将本仓库自带的示例 skills 安装到 workspace 的 generated skills root（`.skills_runtime_sdk/skills`）。

    参数：
    - workspace_root：工作区根目录（与上游 FileStorage 的 workspace_root 对齐；测试可注入临时目录）。

    返回：
    - 本次实际安装的 skill 目录列表（已存在的不会覆盖/重复安装）。
    """

    target_root = (workspace_root / ".skills_runtime_sdk" / "skills").resolve()
    target_root.mkdir(parents=True, exist_ok=True)

    if not _BUNDLE_DIR.exists():
        return []

    installed: List[Path] = []
    for src_dir in sorted(_BUNDLE_DIR.iterdir()):
        if not src_dir.is_dir():
            continue
        dst_dir = target_root / src_dir.name
        if dst_dir.exists():
            continue
        shutil.copytree(src_dir, dst_dir)
        installed.append(dst_dir)

    return installed

