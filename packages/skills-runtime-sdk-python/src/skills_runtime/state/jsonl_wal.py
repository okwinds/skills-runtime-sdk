"""
JSONL WAL（Write-Ahead Log）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/state.md`：Phase 2 JSONL WAL，逐行存储 `AgentEvent`

实现约定（M1 最小闭环）：
- `append()` 返回值为 **0-based 行号**（line index），用于恢复/fork 指定位置。
- 文件为 append-only；不做 compaction、不做并发写保证（后续阶段再增强）。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, TextIO

from skills_runtime.core.contracts import AgentEvent


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
        """

        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._next_index = self._scan_next_index()
        # 复用同一文件句柄，避免每次 append 打开/关闭文件造成额外 syscalls。
        self._fh: Optional[TextIO] = self.path.open("a", encoding="utf-8")

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

    def append(self, event: AgentEvent) -> int:
        """
        追加一条事件，返回其 line index（0-based）。

        说明：
        - 本方法会将事件序列化为单行 JSON（by_alias=True），并追加换行。
        - 返回的 index 在单进程内单调递增；若外部进程同时写同一文件，本实现不保证。
        """

        payload = event.model_dump(by_alias=True, exclude_none=True)
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            index = self._next_index
            if self._fh is None or self._fh.closed:
                self._fh = self.path.open("a", encoding="utf-8")
            self._fh.write(line)
            self._fh.write("\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._next_index += 1
        return index

    def iter_events(self, *, run_id: Optional[str] = None) -> Iterator[AgentEvent]:
        """按文件顺序迭代 WAL 中的事件（可选按 run_id 过滤）。"""

        if not self.path.exists():
            return iter(())

        def _iter() -> Iterator[AgentEvent]:
            """内部生成器：逐行读取 JSONL 并反序列化为 `AgentEvent`。"""

            with self.path.open("r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    ev = AgentEvent.model_validate(obj)
                    if run_id is not None and ev.run_id != run_id:
                        continue
                    yield ev

        return _iter()

    def close(self) -> None:
        """关闭 WAL 句柄（释放 fd）。"""

        with self._lock:
            if self._fh is None:
                return
            try:
                self._fh.close()
            except OSError:
                pass
            finally:
                self._fh = None

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
