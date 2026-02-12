from __future__ import annotations

from pathlib import Path
from typing import Optional

from agent_sdk import bootstrap as agent_bootstrap


def load_dotenv_for_workspace(*, workspace_root: Path) -> Optional[Path]:
    """
    为指定 workspace_root 加载 `.env`（若存在），不覆盖进程外已注入的环境变量。

    参数：
    - workspace_root：工作区根目录

    返回：
    - 实际加载的 env 文件路径（未加载则返回 None）
    """

    return agent_bootstrap.load_dotenv_if_present(workspace_root=workspace_root, override=False)

