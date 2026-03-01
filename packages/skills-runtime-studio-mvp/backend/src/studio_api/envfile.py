from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from skills_runtime import bootstrap as agent_bootstrap


def load_dotenv_for_workspace(*, workspace_root: Path) -> Optional[Path]:
    """
    为指定 workspace_root 加载 `.env`（若存在），不覆盖进程外已注入的环境变量。

    参数：
    - workspace_root：工作区根目录

    返回：
    - 实际加载的 env 文件路径（未加载则返回 None）
    """

    env_file, dotenv_env = agent_bootstrap.load_dotenv_if_present(workspace_root=workspace_root, override=False)
    os.environ.update(dotenv_env)
    return env_file
