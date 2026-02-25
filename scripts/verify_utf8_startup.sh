#!/usr/bin/env bash
set -euo pipefail

# 最小启动验证（不依赖 pytest）。
#
# 目的（对齐 BL-006）：
# - 在 `C` locale / 特定 conda 环境下，stdout/stderr 可能默认为 ASCII；
# - CLI `--help`/JSON 输出包含非 ASCII（例如中文全角符号）时，若未 reconfigure，可能触发 UnicodeEncodeError；
# - 本脚本用于快速验证 “CLI 启动期 UTF-8 兜底” 是否生效。

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_SRC="$REPO_ROOT/packages/skills-runtime-sdk-python/src"

echo "[verify] workspace: $REPO_ROOT"
echo "[verify] PYTHONPATH: $SDK_SRC"

LANG=C LC_ALL=C PYTHONIOENCODING=ascii PYTHONPATH="$SDK_SRC" \
  python3 -m skills_runtime.cli.main --help >/dev/null

echo "[ok] CLI --help works in C locale (ascii IO)"

