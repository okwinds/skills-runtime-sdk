#!/usr/bin/env bash
set -euo pipefail

# 统一离线回归入口（UTF-8 locale + pytest）。
#
# 说明：
# - 某些用例依赖 UTF-8 locale（例如输出/文件编码一致性）。
# - 主线默认仅跑：root / skills-runtime-sdk-python 两套回归。
# - legacy 归档内容默认不跑；可用 INCLUDE_LEGACY=1 显式启用。

# 强制使用 UTF-8 locale（某些环境默认是 C/ascii，会导致 Python 在含中文路径下崩溃）。
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"
# 兜底：即使 locale 不生效，也强制 Python 使用 UTF-8 mode。
export PYTHONUTF8="1"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

pytest -q
pytest -q packages/skills-runtime-sdk-python/tests

if [[ "${INCLUDE_LEGACY:-0}" == "1" ]]; then
  pytest -q legacy/skills-runtime-sdk-web-mvp-python/tests
fi
