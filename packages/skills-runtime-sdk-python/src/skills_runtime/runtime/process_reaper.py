"""
进程清理服务（orphan cleanup）。

职责：
- 启动期读取 exec_registry.json，识别并终止残留的子进程
- 提供进程存活检测与终止能力

约束：
- 仅在 server 启动期调用一次（在 accept loop 之前）
- 不持有长期状态；调用完成后可丢弃
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, Dict

from skills_runtime.runtime.exec_registry_io import read_exec_registry, write_exec_registry

logger = logging.getLogger(__name__)


class ProcessReaper:
    """进程清理器（启动期使用）。"""

    def __init__(self, *, exec_registry_path: Path) -> None:
        """
        创建进程清理器。

        参数：
        - exec_registry_path：exec_registry.json 的绝对路径
        """
        self._exec_registry_path = Path(exec_registry_path).resolve()

    def _read_exec_registry(self, workspace_root: Path) -> Dict[str, Any]:
        """
        读取 exec registry（用于 orphan cleanup 与 status 可观测）。

        返回：
        - dict：至少包含 `exec_sessions`（mapping）
        """
        return read_exec_registry(
            exec_registry_path=self._exec_registry_path,
            workspace_root=workspace_root,
        )

    def _write_exec_registry(self, obj: Dict[str, Any]) -> None:
        """
        原子写入 exec registry（best-effort）。

        参数：
        - obj：registry dict
        """
        write_exec_registry(exec_registry_path=self._exec_registry_path, obj=obj)

    def pid_alive(self, pid: int) -> bool:
        """判断 pid 是否存活（best-effort）。"""
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False

    def ps_env_contains_marker(self, pid: int, marker: str) -> bool:
        """
        通过 `ps eww -p <pid>` 判断环境变量中是否包含 marker（best-effort）。

        说明：
        - 用于降低"pid 复用误杀"的风险；
        - 若命令不可用或权限不足，返回 False（由上层决定是否 fallback）。
        """
        try:
            import subprocess

            cp = subprocess.run(  # noqa: S603
                ["ps", "eww", "-p", str(int(pid))],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out = (cp.stdout or "") + "\n" + (cp.stderr or "")
            return str(marker) in out
        except OSError:
            return False

    def kill_process_group(self, pid: int) -> bool:
        """
        终止进程组（best-effort）。

        参数：
        - pid：进程 pid（也是 pgid）

        返回：
        - bool：是否发出了信号（不代表一定成功）
        """
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except OSError:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                return False

        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            if not self.pid_alive(pid):
                return True
            time.sleep(0.05)

        # 最后兜底：SIGKILL
        try:
            os.killpg(int(pid), signal.SIGKILL)
        except OSError:
            with contextlib.suppress(OSError):
                os.kill(int(pid), signal.SIGKILL)
        return True

    def orphan_cleanup_on_startup(self, *, workspace_root: Path) -> Dict[str, Any]:
        """
        启动期 orphan cleanup（crash/restart 兜底）。

        语义：
        - 读取 registry 中记录的 pids；
        - 仅在 marker（或未来更强身份校验）确认后再 kill；
        - cleanup 后清空 registry（避免无限重试与误判）。

        返回：
        - dict：清理结果（ok, killed, skipped, errors）
        """
        reg = self._read_exec_registry(workspace_root)
        sessions = reg.get("exec_sessions") or {}
        if not isinstance(sessions, dict) or not sessions:
            return {"ok": True, "killed": 0, "skipped": 0, "errors": []}

        killed = 0
        skipped = 0
        errors: list[str] = []
        remaining: Dict[str, Any] = {}

        for sid, item in list(sessions.items()):
            if not isinstance(item, dict):
                skipped += 1
                continue
            pid = int(item.get("pid") or 0)
            marker = str(item.get("marker") or "").strip()
            argv = item.get("argv") or []
            argv0 = str(argv[0]) if isinstance(argv, list) and argv else ""
            if pid <= 0:
                skipped += 1
                continue
            if not self.pid_alive(pid):
                killed += 1  # 视为已无残留（无需保留条目）
                continue

            verified = False
            if marker:
                verified = self.ps_env_contains_marker(pid, marker)

            if not verified:
                skipped += 1
                # 进程存活但无法验证身份：记录详细日志便于人工排查
                logger.warning(
                    "orphan_cleanup: cannot verify pid=%d (marker=%s, argv0=%s), "
                    "marking for manual cleanup",
                    pid,
                    marker[:8] + "..." if marker else "<none>",
                    argv0[:32] if argv0 else "<none>",
                )
                remaining[str(sid)] = dict(item, needs_manual_cleanup=True, last_seen_alive_ms=int(time.time() * 1000))
                continue

            try:
                if self.kill_process_group(pid):
                    killed += 1
                else:
                    errors.append(f"failed_to_kill pid={pid}")
                    remaining[str(sid)] = dict(item, last_kill_error="failed_to_kill", last_seen_alive_ms=int(time.time() * 1000))
            except (OSError, RuntimeError) as e:
                errors.append(f"kill_error pid={pid} err={e}")
                remaining[str(sid)] = dict(item, last_kill_error=str(e), last_seen_alive_ms=int(time.time() * 1000))

        # 更新 registry：移除已确认不存活或已终止的条目；保留无法验证/终止失败的条目，供人工排障。
        reg["exec_sessions"] = remaining
        reg["updated_at_ms"] = int(time.time() * 1000)
        with contextlib.suppress(Exception):
            self._write_exec_registry(reg)

        return {"ok": not errors, "killed": int(killed), "skipped": int(skipped), "errors": list(errors)}
