"""
OS sandbox adapters（Linux bubblewrap / macOS seatbelt）。

参考文档：
- `help/02-config-reference.md`（字段说明）
- `help/sandbox-best-practices.md` / `help/sandbox-best-practices.cn.md`（最佳实践与验证脚本）

说明：
- 本模块只负责“如何把一次 shell_exec 包装成沙箱内执行”的可实现逻辑；
- 不负责 approvals/policy（那是 safety 层职责）；
- 不做产品级容错：当 sandbox 被要求但不可用时，应返回明确可定位的错误。
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Protocol


@dataclass(frozen=True)
class PreparedCommand:
    """
    沙箱准备后的命令（可直接交给 Executor 执行）。

    字段：
    - argv：可执行命令 argv
    - cwd：Executor 进程的工作目录（host 视角）
    """

    argv: list[str]
    cwd: Path


class SandboxAdapter(Protocol):
    """
    OS sandbox adapter 抽象接口。

    方法：
    - `prepare_shell_exec(...)`：将一次 shell_exec 包装为沙箱内执行的命令形式。
    """

    def prepare_shell_exec(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Optional[Mapping[str, str]],
        workspace_root: Path,
    ) -> PreparedCommand:
        """
        将命令包装为“沙箱内执行”形式。

        参数：
        - argv：原始命令 argv
        - cwd：host 上的工作目录（已通过 workspace_root 限制）
        - env：环境变量（当前 adapter 不做清洗；由上层决定是否需要最小化）
        - workspace_root：workspace 根目录（用于 Linux bubblewrap bind 与路径映射）
        """

        ...


class SeatbeltSandboxAdapter:
    """
    macOS seatbelt（sandbox-exec）adapter。

    实现方式：
    - 使用 `sandbox-exec -p <profile> <cmd...>`。
    - profile 文本必须由业务提供（框架不做“猜测式宽松”）。
    """

    def __init__(self, *, sandbox_exec_path: str = "sandbox-exec", profile: str) -> None:
        """
        创建 seatbelt adapter。

        参数：
        - sandbox_exec_path：`sandbox-exec` 命令路径（默认从 PATH 查找）
        - profile：SBPL profile 文本
        """

        self._sandbox_exec_path = sandbox_exec_path
        self._profile = str(profile or "").strip()

    def is_available(self) -> bool:
        """检查 `sandbox-exec` 是否可用（PATH 或绝对路径）。"""

        if Path(self._sandbox_exec_path).is_absolute():
            return Path(self._sandbox_exec_path).exists()
        return shutil.which(self._sandbox_exec_path) is not None

    def prepare_shell_exec(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Optional[Mapping[str, str]],
        workspace_root: Path,
    ) -> PreparedCommand:
        """
        将命令包装为 `sandbox-exec -p <profile> ...`。

        说明：
        - seatbelt 不改变 rootfs；cwd 仍由 Executor 在 host 侧设置；
        - env 由 Executor 传递；本 adapter 不做强制清理（产品层可加）。
        """

        if not self._profile:
            raise RuntimeError("Seatbelt sandbox profile is empty.")
        if not self.is_available():
            raise RuntimeError("sandbox-exec is not available.")

        sandbox_exec = self._sandbox_exec_path
        wrapped = [sandbox_exec, "-p", self._profile]
        wrapped.extend(argv)
        return PreparedCommand(argv=wrapped, cwd=cwd)


class BubblewrapSandboxAdapter:
    """
    Linux bubblewrap（bwrap）adapter（最小可实现）。

    说明：
    - 本实现目标是提供一个可复用的“最小 wrapper”，而不是追求发行版无差异的完美隔离；
    - 真正生产化的 filesystem allowlist、tmpfs、用户映射等策略应由业务通过配置扩展。
    """

    def __init__(self, *, bwrap_path: str = "bwrap", unshare_net: bool = True) -> None:
        """
        创建 bubblewrap adapter。

        参数：
        - bwrap_path：bwrap 命令路径（默认从 PATH 查找）
        - unshare_net：是否默认隔离网络（restricted sandbox 推荐 true）
        """

        self._bwrap_path = bwrap_path
        self._unshare_net = bool(unshare_net)

    def is_available(self) -> bool:
        """检查 `bwrap` 是否可用（PATH 或绝对路径）。"""

        if Path(self._bwrap_path).is_absolute():
            return Path(self._bwrap_path).exists()
        return shutil.which(self._bwrap_path) is not None

    def prepare_shell_exec(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Optional[Mapping[str, str]],
        workspace_root: Path,
    ) -> PreparedCommand:
        """
        将命令包装为 bwrap 内执行（最小约束）：
        - bind workspace_root 到 /work（rw）
        - 只读 bind 常见系统目录（best-effort，存在才 bind）
        - 可选 `--unshare-net` 禁用网络
        - 在沙箱内 chdir 到 /work/<relative cwd>
        """

        if not self.is_available():
            raise RuntimeError("bwrap is not available.")

        root = Path(workspace_root).resolve()
        host_cwd = Path(cwd).resolve()
        if not host_cwd.is_relative_to(root):
            raise RuntimeError("cwd must be within workspace_root for bubblewrap sandbox.")

        rel = host_cwd.relative_to(root).as_posix()
        sandbox_cwd = "/work" if not rel or rel == "." else f"/work/{rel}"

        args: list[str] = [self._bwrap_path, "--die-with-parent", "--new-session"]
        if self._unshare_net:
            args.append("--unshare-net")

        # 基础伪文件系统
        args.extend(["--proc", "/proc"])
        args.extend(["--dev", "/dev"])

        # 常见系统目录只读 bind（存在才 bind，避免在不同发行版上直接失败）
        for p in ("/usr", "/bin", "/lib", "/lib64", "/etc"):
            if Path(p).exists():
                args.extend(["--ro-bind", p, p])

        # workspace：rw bind 到 /work
        args.extend(["--bind", str(root), "/work"])
        args.extend(["--chdir", sandbox_cwd])

        # exec
        args.append("--")
        args.extend(argv)

        # 让 bubblewrap 进程在 host 上也以 workspace_root 为 cwd（日志/相对路径更稳定）
        return PreparedCommand(argv=args, cwd=root)


def create_default_os_sandbox_adapter(
    *,
    platform: Optional[str] = None,
    mode: str,
    seatbelt_profile: str,
    bubblewrap_bwrap_path: str,
    bubblewrap_unshare_net: bool,
) -> Optional[SandboxAdapter]:
    """
    基于平台与配置创建默认 OS sandbox adapter。

    参数：
    - platform：用于测试注入；缺省使用 `sys.platform`
    - mode：`auto|none|seatbelt|bubblewrap`

    返回：
    - SandboxAdapter 或 None（mode=none / 平台不匹配 / 不可用时返回 None）
    """

    plat = platform or sys.platform
    mm = str(mode or "auto").strip().lower()
    if mm == "none":
        return None

    if mm == "auto":
        if plat.startswith("darwin"):
            mm = "seatbelt"
        elif plat.startswith("linux"):
            mm = "bubblewrap"
        else:
            return None

    if mm == "seatbelt":
        adapter = SeatbeltSandboxAdapter(profile=seatbelt_profile)
        return adapter if adapter.is_available() else None

    if mm == "bubblewrap":
        adapter = BubblewrapSandboxAdapter(bwrap_path=bubblewrap_bwrap_path, unshare_net=bubblewrap_unshare_net)
        return adapter if adapter.is_available() else None

    return None
