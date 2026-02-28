"""共享工具函数（消除跨模块重复）。"""
from __future__ import annotations

from datetime import datetime, timezone


def now_rfc3339() -> str:
    """返回当前 UTC 时间的 RFC3339 字符串（以 Z 结尾）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
