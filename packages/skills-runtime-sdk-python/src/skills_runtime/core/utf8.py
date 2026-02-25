"""
UTF-8 启动健壮性工具（CLI/脚本入口用）。

目的（对齐 backlog：BL-006）：
- 在 `C` locale / 某些 conda 环境下，stdout/stderr 默认编码可能为 ASCII；
- CLI/脚本输出包含非 ASCII（例如中文）时，可能触发 `UnicodeEncodeError`；
- 本模块提供 best-effort 的 stdio reconfigure，避免“启动即崩”。

说明：
- 该能力只能修复“输出编码”问题，无法在 Python 解释器启动后改变其内部默认编码策略；
- 入口应尽早调用（在 argparse/help 或任何 print 之前）。
"""

from __future__ import annotations


def ensure_utf8_stdio() -> None:
    """
    best-effort 将 stdout/stderr reconfigure 为 UTF-8。

    行为：
    - 若当前 Python 版本/流对象支持 `reconfigure()`，则设置 `encoding="utf-8", errors="replace"`；
    - 任何异常均 fail-open（不阻断程序启动）。
    """

    try:
        import sys

        for stream in (sys.stdout, sys.stderr):
            try:
                reconfigure = getattr(stream, "reconfigure", None)
                if callable(reconfigure):
                    reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                continue
    except Exception:
        return

