"""
JSONL WAL（Write-Ahead Log）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/state.md`：Phase 2 JSONL WAL，逐行存储 `AgentEvent`

实现约定（M1 最小闭环）：
- `append()` 返回值为 **0-based 行号**（line index），用于恢复/fork 指定位置。
- 文件为 append-only；不做 compaction。
"""

from __future__ import annotations

import contextlib
import logging
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, Optional, TextIO

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.errors import StateError

try:
    import fcntl
except ImportError:  # pragma: no cover - 非 POSIX 平台兜底
    fcntl = None  # type: ignore[assignment]

# 终态事件集合：这些事件落盘后才视为 run 持久化完成，MUST fsync。
# delta 类事件（如 llm_response_delta、tool_call_started）只需 flush，不强制 fsync。
# 注：budget 耗尽实际经 RunContext.emit_budget_exceeded 以 type="run_failed" +
# payload.error_kind="budget_exceeded" 发出（见 run_context.py），"budget_exceeded"
# type 当前无直接 emit 点，保留于此为前瞻性兜底（若未来直接 emit 该 type 仍触发 fsync）。
_TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({
    "run_completed",
    "run_failed",
    "run_cancelled",
    "run_waiting_human",
    "budget_exceeded",
})
_TAIL_SCAN_BLOCK_SIZE = 64 * 1024
_CORRUPT_LINE_SNIPPET_LIMIT = 200


class WalCorruptError(StateError):
    """WAL 中已换行落盘的 JSON 行损坏，读取方必须 fail-loud。"""

    def __init__(self, *, path: Path, line_no: int, snippet: str, cause: Exception) -> None:
        """
        创建 WAL 损坏异常。

        参数：
        - path：损坏 WAL 文件路径；
        - line_no：1-based 行号；
        - snippet：原始行片段，已由调用方做截断展示；
        - cause：底层 JSON 解析异常。
        """

        super().__init__(
            f"WAL corrupt line (path={path} line={line_no} snippet={snippet!r}): {cause}"
        )
        self.path = Path(path)
        self.line_no = int(line_no)
        self.snippet = snippet
        self.cause = cause


@dataclass
class JsonlWal:
    """
    追加写 JSONL 的 WAL。

    参数：
    - path：WAL 文件路径（例如 `.skills_runtime_sdk/runs/<run_id>/events.jsonl`）
    """

    path: Path

    def __post_init__(self) -> None:
        """
        初始化 WAL：确保目录存在并计算下一个写入 index。

        说明：
        - `dataclass` 初始化后会调用该方法；
        - `_next_index` 通过扫描现有文件行数得到（0-based）。
        - 构造副作用不变量：打开既有 WAL 时会持锁修复掉电产生的尾部半行
         （`_repair_truncated_tail`），并 best-effort fsync 父目录；故构造非幂等只读。
        - 构造期 I/O 异常会关闭已打开句柄再传播，避免半构造对象与 fd 泄漏。
        """

        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        was_missing = not self.path.exists()
        self._lock = threading.RLock()
        self._lock_path = self.path.with_name(f"{self.path.name}.lock")
        self._lock_fh: Optional[TextIO] = self._lock_path.open("a+", encoding="utf-8")
        # 复用同一文件句柄，避免每次 append 打开/关闭文件造成额外 syscalls。
        self._fh: Optional[TextIO] = self.path.open("a", encoding="utf-8")
        try:
            if was_missing:
                self._fsync_parent_directory()
            self._repair_truncated_tail()
            self._next_index = self._scan_next_index()
            self._observed_signature = self._stat_signature()
        except BaseException:
            # 构造期 I/O（repair/fsync/scan）失败时关闭已打开句柄，避免泄漏 + 半构造对象。
            for fh in (self._fh, self._lock_fh):
                try:
                    if fh is not None and not fh.closed:
                        fh.close()
                except OSError:
                    pass
            raise

    def locator(self) -> str:
        """
        返回 WAL 定位符（locator）。

        约束：
        - 默认返回 WAL 文件的绝对路径字符串（不强制使用 file://）。
        """

        try:
            return str(Path(self.path).resolve())
        except OSError:
            return str(self.path)

    def _scan_next_index(self) -> int:
        """扫描现有文件以获得下一个可用 line index（0-based）。"""

        if not self.path.exists():
            return 0
        count = 0
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    def _stat_signature(self) -> tuple[int, int]:
        """返回 `(size, mtime_ns)` 签名，用于检测外部进程是否已写入 WAL。"""

        try:
            st = self.path.stat()
        except FileNotFoundError:
            return (0, 0)
        return (int(st.st_size), int(getattr(st, "st_mtime_ns", 0)))

    def _run_id_for_log(self) -> str:
        """返回用于 WAL 修复日志的 run_id 近似值。"""

        return self.path.parent.name or "unknown"

    def _fsync_parent_directory(self) -> None:
        """
        新建 WAL 文件后 best-effort fsync 父目录，持久化目录项。

        平台不支持目录 fd 或 fsync 失败时只记录 warning，不阻断 WAL 初始化。
        """

        fd: Optional[int] = None
        try:
            fd = os.open(self.path.parent, os.O_RDONLY)
            os.fsync(fd)
        except OSError as exc:
            logging.warning(
                "WAL parent directory fsync skipped (path=%s parent=%s): %s",
                self.path,
                self.path.parent,
                exc,
            )
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError as exc:
                    logging.warning(
                        "WAL parent directory close after fsync skipped (path=%s parent=%s): %s",
                        self.path,
                        self.path.parent,
                        exc,
                    )

    def _repair_truncated_tail(self) -> None:
        """
        持锁修复 WAL 尾部真半行。

        只按字节级换行边界判断完整行：无换行尾部会被截断；带换行但 JSON
        不可解析的行保留给 `iter_events()` 抛 `WalCorruptError`。
        """

        with self._lock:
            with self._process_append_lock():
                self._repair_truncated_tail_locked()
                self._observed_signature = self._stat_signature()

    def _repair_truncated_tail_locked(self) -> None:
        """在已持有线程锁和进程锁时执行尾部半行截断。"""

        if not self.path.exists():
            return

        with self.path.open("r+b") as repair_fh:
            repair_fh.seek(0, os.SEEK_END)
            file_size = repair_fh.tell()
            if file_size == 0:
                return

            last_newline_pos = self._find_last_newline_pos(repair_fh, file_size)
            run_id = self._run_id_for_log()
            if last_newline_pos is None:
                repair_fh.truncate(0)
                logging.warning(
                    "WAL truncated whole half-line file (path=%s run_id=%s truncated_bytes=%s)",
                    self.path,
                    run_id,
                    file_size,
                )
                return

            truncate_at = last_newline_pos + 1
            truncated_bytes = file_size - truncate_at
            if truncated_bytes <= 0:
                return

            repair_fh.truncate(truncate_at)
            logging.warning(
                "WAL truncated tail half-line (path=%s run_id=%s truncated_bytes=%s)",
                self.path,
                run_id,
                truncated_bytes,
            )

    def _find_last_newline_pos(self, fh: BinaryIO, file_size: int) -> Optional[int]:
        """
        从文件尾按块向前查找最后一个换行字节的位置。

        参数：
        - fh：以二进制读写方式打开的 WAL 句柄；
        - file_size：当前文件大小，单位字节。
        """

        cursor = file_size
        while cursor > 0:
            read_size = min(_TAIL_SCAN_BLOCK_SIZE, cursor)
            cursor -= read_size
            fh.seek(cursor)
            block = fh.read(read_size)
            offset = block.rfind(b"\n")
            if offset >= 0:
                return cursor + offset
        return None

    def _format_corrupt_snippet(self, raw_line: str) -> str:
        """返回用于异常消息的原始行截断片段。"""

        snippet = raw_line.rstrip("\n").rstrip("\r")
        snippet = snippet.replace("\r", "\\r").replace("\n", "\\n")
        if len(snippet) > _CORRUPT_LINE_SNIPPET_LIMIT:
            return f"{snippet[:_CORRUPT_LINE_SNIPPET_LIMIT]}..."
        return snippet

    @contextlib.contextmanager
    def _process_append_lock(self):
        """
        进程级 append 锁（best-effort）。

        说明：
        - POSIX 下使用 `fcntl.flock`，用于多进程共享 WAL 时对齐 line index；
        - 非 POSIX 平台退化为仅进程内线程安全，不破坏既有语义。
        """

        if fcntl is None or self._lock_fh is None or self._lock_fh.closed:
            yield
            return
        fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)

    def append(self, event: AgentEvent, *, force_fsync: bool = False) -> int:
        """
        追加一条事件，返回其 line index（0-based）。

        说明：
        - 本方法会将事件序列化为单行 JSON（by_alias=True），并追加换行。
        - 在同一 WAL 文件被多个进程共享时，append 会先进入进程级锁并在必要时重扫 index；
          correctness 优先于极限吞吐。
        - fsync 分级：
          - `force_fsync=True`：调用方显式要求持久化（如终态事件写入路径）。
          - `force_fsync=False`（默认）：自动判断——终态事件（run_completed/
            run_failed/run_cancelled/budget_exceeded）仍 fsync；delta 事件只 flush。
          两种路径均保证：非终态 delta 事件不会因额外 fsync 引入延迟。
        """

        payload = event.model_dump(by_alias=True, exclude_none=True)
        line = json.dumps(payload, ensure_ascii=False)
        is_terminal = force_fsync or (event.type in _TERMINAL_EVENT_TYPES)
        with self._lock:
            with self._process_append_lock():
                current_signature = self._stat_signature()
                if current_signature != self._observed_signature:
                    self._next_index = self._scan_next_index()
                    self._observed_signature = current_signature
                index = self._next_index
                if self._fh is None or self._fh.closed:
                    self._fh = self.path.open("a", encoding="utf-8")
                self._fh.write(line)
                self._fh.write("\n")
                self._fh.flush()
                if is_terminal:
                    os.fsync(self._fh.fileno())
                self._next_index = index + 1
                self._observed_signature = self._stat_signature()
        return index

    def iter_events(self, *, run_id: Optional[str] = None) -> Iterator[AgentEvent]:
        """按文件顺序迭代 WAL 中的事件（可选按 run_id 过滤）。"""

        if not self.path.exists():
            return iter(())

        def _iter() -> Iterator[AgentEvent]:
            """内部生成器：逐行读取 JSONL 并反序列化为 `AgentEvent`。"""

            invalid_json_lines = 0
            non_object_lines = 0
            invalid_event_lines = 0

            with self.path.open("r", encoding="utf-8") as f:
                for line_no, raw_line in enumerate(f, start=1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception as exc:
                        if not raw_line.endswith("\n"):
                            logging.warning(
                                "WAL trailing half-line is not valid JSON; skipped (path=%s line=%s): %s",
                                self.path,
                                line_no,
                                exc,
                            )
                            invalid_json_lines += 1
                            continue
                        raise WalCorruptError(
                            path=self.path,
                            line_no=line_no,
                            snippet=self._format_corrupt_snippet(raw_line),
                            cause=exc,
                        ) from exc

                    # 前向兼容：允许未来 writer 增加未知顶层字段；读取时忽略未知字段，避免崩溃整段迭代。
                    if not isinstance(obj, dict):
                        logging.warning("WAL line is not a JSON object (path=%s line=%s)", self.path, line_no)
                        non_object_lines += 1
                        continue
                    filtered = {
                        k: obj[k]
                        for k in (
                            "type",
                            "timestamp",
                            "run_id",
                            "turn_id",
                            "step_id",
                            "payload",
                        )
                        if k in obj
                    }
                    try:
                        ev = AgentEvent.model_validate(filtered)
                    except Exception as exc:
                        logging.warning("WAL line failed to parse as AgentEvent (path=%s line=%s): %s", self.path, line_no, exc)
                        invalid_event_lines += 1
                        continue

                    if run_id is not None and ev.run_id != run_id:
                        continue
                    yield ev

            # 可观测性：给出稳定的“跳过计数”汇总，便于 metrics/replay 排障。
            skipped = invalid_json_lines + non_object_lines + invalid_event_lines
            if skipped:
                logging.warning(
                    "WAL iter_events skipped unparseable lines (path=%s skipped=%s invalid_json=%s non_object=%s invalid_event=%s)",
                    self.path,
                    skipped,
                    invalid_json_lines,
                    non_object_lines,
                    invalid_event_lines,
                )

        return _iter()

    def close(self) -> None:
        """关闭 WAL 句柄（释放 fd）。"""

        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                finally:
                    self._fh = None
            if self._lock_fh is not None:
                try:
                    self._lock_fh.close()
                except OSError:
                    pass
                finally:
                    self._lock_fh = None

    def __enter__(self) -> "JsonlWal":
        """上下文管理器入口：返回 self（便于 with 使用）。"""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        """上下文管理器退出：确保关闭文件句柄。"""
        self.close()

    def __del__(self) -> None:
        """析构兜底：尽力 close，避免文件句柄泄漏。"""
        # 防御性兜底：确保文件句柄不因忘记 close 而泄漏（CPython 下通常可及时回收）。
        try:
            self.close()
        except Exception:
            pass
