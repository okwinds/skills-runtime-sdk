#!/usr/bin/env bash
set -euo pipefail

# macOS seatbelt（sandbox-exec）通路探测（可选集成验证）。
#
# 说明：
# - 本脚本不进入离线回归门禁；只用于在 macOS 上快速确认 sandbox-exec 可用。
# - profile 使用最小可跑通示例：`(version 1) (allow default)`；生产环境应使用更严格的 profile。

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[sandbox] seatbelt probe: skipped (not macOS)"
  exit 0
fi

if ! command -v sandbox-exec >/dev/null 2>&1; then
  echo "[sandbox] seatbelt probe: sandbox-exec not found"
  exit 1
fi

sandbox-exec -p '(version 1) (allow default)' /bin/echo 'seatbelt_ok'

