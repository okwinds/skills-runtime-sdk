"""
Executor（命令/脚本/文件系统执行引擎）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/executor.md`

本模块聚焦 Phase 2 的最小可用闭环：
- `Executor.run_command(...)`：执行 argv 命令
- 标准化 `CommandResult`：stdout/stderr/exit_code/timeout/truncated/error_kind 等

说明：
- 为避免子进程 stdout/stderr 输出过大导致内存膨胀，本实现采用“尾部截断”策略：
  - 单独限制 stdout/stderr 的最大字节数（保留尾部）
  - 再对 combined bytes 施加上限（优先保留 stderr，其次 stdout）
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field


class CommandResult(BaseModel):
    """
    命令执行结果（结构化）。

    字段说明（最小集合）：
    - ok：是否执行成功（通常为 exit_code==0 且未超时）
    - exit_code：进程退出码；超时/被取消时为 None
    - stdout/stderr：捕获到的输出（可能被截断）
    - duration_ms：耗时（毫秒）
    - timeout：是否因超时被终止
    - truncated：stdout/stderr 是否发生截断（任一发生即 true）
    - error_kind：错误分类（例如 timeout/validation/exit_code/unknown）
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(default=0, ge=0)
    timeout: bool = False
    truncated: bool = False
    error_kind: Optional[str] = None


class _TailRingBuffer:
    """保留尾部的有界字节缓冲（用于截断策略）。"""

    def __init__(self, max_bytes: int) -> None:
        """
        创建一个“只保留尾部”的字节缓冲区。

        参数：
        - `max_bytes`：允许保留的最大字节数；为 0 时表示不保留任何输出（但会标记 truncated）。
        """

        if max_bytes < 0:
            raise ValueError("max_bytes 必须 >= 0")
        self._max_bytes = max_bytes
        self._buf = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        """追加字节；超出上限时丢弃头部，保留尾部。"""

        if not chunk:
            return
        if self._max_bytes == 0:
            self.truncated = True
            return

        if len(chunk) >= self._max_bytes:
            self._buf[:] = chunk[-self._max_bytes :]
            self.truncated = True
            return

        overflow = len(self._buf) + len(chunk) - self._max_bytes
        if overflow > 0:
            del self._buf[:overflow]
            self.truncated = True
        self._buf.extend(chunk)

    def get_bytes(self) -> bytes:
        """获取当前缓冲内容（尾部片段）。"""

        return bytes(self._buf)


def _decode_bytes(data: bytes) -> str:
    """将字节解码为 UTF-8 文本；非法字节替换为 U+FFFD。"""

    return data.decode("utf-8", errors="replace")


class Executor:
    """
    执行器（Phase 2：最小可用）。

    参数：
    - max_stdout_bytes/max_stderr_bytes：分别限制 stdout/stderr 记录的最大字节数（尾部保留）
    - max_combined_bytes：限制 stdout+stderr 的总记录字节数（尾部保留；优先保留 stderr）
    - terminate_grace_ms：超时后 SIGTERM→SIGKILL 的宽限时间（毫秒）
    - truncate_marker：截断提示（会被插入到输出最前部，提示前文被省略）
    """

    def __init__(
        self,
        *,
        max_stdout_bytes: int = 64 * 1024,
        max_stderr_bytes: int = 64 * 1024,
        max_combined_bytes: int = 128 * 1024,
        terminate_grace_ms: int = 200,
        truncate_marker: str = "...<truncated>\n",
    ) -> None:
        """
        创建执行器并配置输出截断策略与超时终止策略。

        约束：
        - 所有 `max_*_bytes` 必须 >= 0；
        - `terminate_grace_ms` 必须 >= 0；
        - 本类不做命令白名单/危险性判断（该职责属于 safety/approvals 层）。
        """

        if max_stdout_bytes < 0 or max_stderr_bytes < 0 or max_combined_bytes < 0:
            raise ValueError("max_*_bytes 必须 >= 0")
        if terminate_grace_ms < 0:
            raise ValueError("terminate_grace_ms 必须 >= 0")
        self._max_stdout_bytes = max_stdout_bytes
        self._max_stderr_bytes = max_stderr_bytes
        self._max_combined_bytes = max_combined_bytes
        self._terminate_grace_ms = terminate_grace_ms
        self._truncate_marker = truncate_marker

    def run_command(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: Optional[Mapping[str, str]] = None,
        timeout_ms: int = 60_000,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> CommandResult:
        """
        执行 argv 命令并捕获结果。

        参数：
        - argv：命令与参数（argv 形式，至少 1 项）
        - cwd：工作目录（必须存在且为目录）
        - env：追加/覆盖的环境变量（会覆盖 os.environ 同名项）
        - timeout_ms：超时毫秒数；超时后会尝试 SIGTERM→SIGKILL

        返回：
        - `CommandResult`：包含 stdout/stderr/exit_code/timeout/truncated 等结构化字段
        """

        start = time.monotonic()
        if not argv:
            return CommandResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="argv 不能为空",
                duration_ms=0,
                timeout=False,
                truncated=False,
                error_kind="validation",
            )

        cwd_path = Path(cwd)
        if not cwd_path.exists() or not cwd_path.is_dir():
            return CommandResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr=f"cwd 不存在或不是目录：{cwd_path}",
                duration_ms=0,
                timeout=False,
                truncated=False,
                error_kind="validation",
            )

        if timeout_ms < 1:
            return CommandResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="timeout_ms 必须 >= 1",
                duration_ms=0,
                timeout=False,
                truncated=False,
                error_kind="validation",
            )

        merged_env = dict(os.environ)
        if env:
            merged_env.update({str(k): str(v) for k, v in env.items()})

        stdout_buf = _TailRingBuffer(self._max_stdout_bytes)
        stderr_buf = _TailRingBuffer(self._max_stderr_bytes)

        def _drain_stream(stream: Optional[object], buf: _TailRingBuffer) -> None:
            """持续读取子进程 stdout/stderr 并写入尾部缓冲（用于后台线程）。"""

            if stream is None:
                return
            # `Popen.stdout/stderr` 是 `io.BufferedReader`；使用 read() 分块读取即可。
            while True:
                try:
                    chunk = stream.read(4096)  # type: ignore[attr-defined]
                except Exception:
                    return
                if not chunk:
                    return
                buf.append(chunk)

        popen_kwargs: dict = {
            "cwd": str(cwd_path),
            "env": merged_env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": False,
        }

        # 让超时 kill 更可靠：尽量让子进程成为新的进程组 leader。
        if os.name != "nt":
            popen_kwargs["preexec_fn"] = os.setsid  # type: ignore[assignment]
        else:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        try:
            proc = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603
        except FileNotFoundError as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            return CommandResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr=str(e),
                duration_ms=duration_ms,
                timeout=False,
                truncated=False,
                error_kind="not_found",
            )
        except Exception as e:  # pragma: no cover (极少数环境差异)
            duration_ms = int((time.monotonic() - start) * 1000)
            return CommandResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr=str(e),
                duration_ms=duration_ms,
                timeout=False,
                truncated=False,
                error_kind="unknown",
            )

        t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, stdout_buf), daemon=True)
        t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, stderr_buf), daemon=True)
        t_out.start()
        t_err.start()

        timeout = False
        cancelled = False
        try:
            # 为支持“硬取消”，采用短超时轮询：
            # - 超时：按原逻辑 terminate
            # - cancel_checker：若返回 true，terminate 并标记 cancelled
            deadline = time.monotonic() + (timeout_ms / 1000.0)
            while True:
                if cancel_checker is not None:
                    try:
                        if cancel_checker():
                            cancelled = True
                            self._terminate_process(proc)
                            break
                    except Exception:
                        # fail-open：取消检测异常不应杀死执行器
                        pass

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timeout = True
                    self._terminate_process(proc)
                    break
                try:
                    proc.wait(timeout=min(0.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except subprocess.TimeoutExpired:
            timeout = True
            self._terminate_process(proc)
        finally:
            t_out.join(timeout=1.0)
            t_err.join(timeout=1.0)
            # 若 reader thread 仍未退出，再关闭管道，避免阻塞。
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr:
                    proc.stderr.close()
            except Exception:
                pass

        duration_ms = int((time.monotonic() - start) * 1000)
        exit_code: Optional[int] = None if (timeout or cancelled) else proc.returncode

        out_b = stdout_buf.get_bytes()
        err_b = stderr_buf.get_bytes()

        combined_truncated = stdout_buf.truncated or stderr_buf.truncated
        out_b2, err_b2, combined_limit_truncated = self._apply_combined_limit(out_b, err_b)
        truncated = combined_truncated or combined_limit_truncated

        stdout_text = _decode_bytes(out_b2)
        stderr_text = _decode_bytes(err_b2)
        if truncated:
            if stdout_text:
                stdout_text = f"{self._truncate_marker}{stdout_text}"
            if stderr_text:
                stderr_text = f"{self._truncate_marker}{stderr_text}"

        if cancelled:
            return CommandResult(
                ok=False,
                exit_code=None,
                stdout=stdout_text,
                stderr=stderr_text,
                duration_ms=duration_ms,
                timeout=False,
                truncated=truncated,
                error_kind="cancelled",
            )

        if timeout:
            return CommandResult(
                ok=False,
                exit_code=None,
                stdout=stdout_text,
                stderr=stderr_text,
                duration_ms=duration_ms,
                timeout=True,
                truncated=truncated,
                error_kind="timeout",
            )

        ok = exit_code == 0
        error_kind = None if ok else "exit_code"
        return CommandResult(
            ok=ok,
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_ms=duration_ms,
            timeout=False,
            truncated=truncated,
            error_kind=error_kind,
        )

    def run_shell(
        self,
        command: str,
        *,
        shell: str = "/bin/bash",
        login: bool = False,
        cwd: Path,
        env: Optional[Mapping[str, str]] = None,
        timeout_ms: int = 60_000,
    ) -> CommandResult:
        """
        以 shell 执行一条命令字符串（Phase 2：基础实现）。

        参数：
        - command：要执行的 shell 命令文本
        - shell：shell 可执行文件路径（例如 /bin/bash）
        - login：是否以 login shell 执行（以 `-l` 传递；不同 shell 语义可能略有差异）
        - cwd/env/timeout_ms：同 `run_command`
        """

        argv = [shell]
        if login:
            argv.append("-l")
        argv.extend(["-c", command])
        return self.run_command(argv, cwd=cwd, env=env, timeout_ms=timeout_ms)

    def _terminate_process(self, proc: subprocess.Popen[bytes]) -> None:
        """
        超时终止子进程：SIGTERM → (grace) → SIGKILL。

        注意：
        - 在 POSIX 下，优先终止进程组（preexec_fn=os.setsid）。
        - 在 Windows 下，使用 terminate/kill。
        """

        if os.name == "nt":
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=self._terminate_grace_ms / 1000.0)
                return
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
            return

        # POSIX：尽量杀进程组
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

        try:
            proc.wait(timeout=self._terminate_grace_ms / 1000.0)
            return
        except Exception:
            pass

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _apply_combined_limit(self, stdout_b: bytes, stderr_b: bytes) -> tuple[bytes, bytes, bool]:
        """
        对 stdout/stderr 的“记录字节数总和”施加上限（尾部保留）。

        策略：
        - 优先保留 stderr（更利于诊断）
        - 再保留 stdout
        """

        max_total = self._max_combined_bytes
        total = len(stdout_b) + len(stderr_b)
        if max_total <= 0 or total <= max_total:
            return stdout_b, stderr_b, False

        # 需要减少的字节数（从头部丢弃，保留尾部）
        drop = total - max_total

        # 先从 stdout 丢弃
        if drop >= len(stdout_b):
            drop -= len(stdout_b)
            stdout_b = b""
        else:
            stdout_b = stdout_b[drop:]
            drop = 0

        # 再从 stderr 丢弃（通常不应发生，除非 stderr 本身就大于 max_total）
        if drop > 0:
            if drop >= len(stderr_b):
                stderr_b = b""
            else:
                stderr_b = stderr_b[drop:]

        return stdout_b, stderr_b, True
