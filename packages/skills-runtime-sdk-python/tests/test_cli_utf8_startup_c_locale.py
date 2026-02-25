from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_cli_help_does_not_crash_in_c_locale(tmp_path: Path) -> None:
    """
    回归：CLI 在 `C` locale（ASCII 默认编码）下启动不应崩溃。

    背景：
    - 在部分 conda/CI 环境中，`LANG/LC_ALL` 可能为 `C`，stdout 默认编码为 ASCII；
    - CLI `--help` 会输出包含中文字符的描述文本，若不做 stdout/stderr reconfigure，可能触发 UnicodeEncodeError。
    """

    repo_root = Path(__file__).resolve().parents[3]
    src = repo_root / "packages" / "skills-runtime-sdk-python" / "src"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    # 强化复现：强制 Python IO 编码为 ASCII（若 CLI 未 reconfigure，会在打印 help 时崩溃）。
    env["PYTHONIOENCODING"] = "ascii"

    p = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "skills_runtime.cli.main", "--help"],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
    )
    assert p.returncode == 0, (p.stdout, p.stderr)

