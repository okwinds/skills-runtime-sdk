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
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

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
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
            self._next_index += 1
        return index

    def iter_events(self) -> Iterator[AgentEvent]:
        """按文件顺序迭代 WAL 中的事件。"""

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
                    yield AgentEvent.model_validate(obj)

        return _iter()
