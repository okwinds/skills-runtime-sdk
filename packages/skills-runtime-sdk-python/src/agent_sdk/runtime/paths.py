from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class RuntimePaths:
    """runtime 目录与关键文件路径集合。"""

    runtime_dir: Path
    socket_path: Path
    server_info_path: Path
    exec_registry_path: Path


def get_runtime_paths(*, workspace_root: Path) -> RuntimePaths:
    """
    获取 runtime 相关路径（均位于 workspace_root/.skills_runtime_sdk/runtime 下）。

    参数：
    - workspace_root：工作区根目录
    """

    ws = Path(workspace_root).resolve()
    runtime_dir = (ws / ".skills_runtime_sdk" / "runtime").resolve()
    socket_path = (runtime_dir / "runtime.sock").resolve()
    # macOS/部分 Unix 的 AF_UNIX 路径长度有上限（常见 ~104 bytes）。
    # workspace_root 若位于较深的临时目录（例如 macOS /private/var/folders/...），
    # 则需要降级到更短的 `/tmp` socket 路径，否则 server 无法启动。
    if len(str(socket_path)) > 90:
        h = hashlib.sha256(str(ws).encode("utf-8", errors="replace")).hexdigest()[:16]
        socket_path = (Path(tempfile.gettempdir()) / f"agent_sdk_runtime_{h}.sock").resolve()
    server_info_path = (runtime_dir / "server.json").resolve()
    exec_registry_path = (runtime_dir / "exec_registry.json").resolve()
    return RuntimePaths(
        runtime_dir=runtime_dir,
        socket_path=socket_path,
        server_info_path=server_info_path,
        exec_registry_path=exec_registry_path,
    )
