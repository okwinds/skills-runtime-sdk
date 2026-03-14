#!/usr/bin/env bash
set -euo pipefail

# DEPRECATED / 示例文件（不要在文档里引导使用它）
#
# 背景：
# - 历史上存在一个 `config.s`，里面包含绝对路径与旧 web-mvp 目录引用，耦合严重且不可移植。
# - 本 MVP 已将可复现配置收敛到仓库内：
#   - LLM overlay：`backend/config/runtime.yaml`
#   - 环境变量：`backend/.env`（由 `studio_api.app` 启动时自动加载；不要提交 secrets）
#
# 推荐启动方式：
# - 后端：`bash backend/scripts/dev.sh`
# - 前端：`npm -C frontend run dev`
#
# 本文件仅用于给开发者提供“如果你非要手动启动”的最小示例；你可以复制为 `config.local.sh` 自用。

STUDIO_PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${STUDIO_PKG_DIR}/../.." && pwd)"
BACKEND_DIR="${STUDIO_PKG_DIR}/backend"

SDK_PY_SRC="${REPO_ROOT}/packages/skills-runtime-sdk-python/src"
STUDIO_BACKEND_SRC="${BACKEND_DIR}/src"

if [[ ! -d "${SDK_PY_SRC}" ]]; then
  echo "ERROR: cannot find SDK python src dir under monorepo root" >&2
  echo "  expected: ${SDK_PY_SRC}" >&2
  echo "  REPO_ROOT=${REPO_ROOT}" >&2
  exit 1
fi

if [[ ! -d "${STUDIO_BACKEND_SRC}" ]]; then
  echo "ERROR: cannot find Studio backend src dir" >&2
  echo "  expected: ${STUDIO_BACKEND_SRC}" >&2
  echo "  BACKEND_DIR=${BACKEND_DIR}" >&2
  exit 1
fi

export PYTHONPATH="${SDK_PY_SRC}:${STUDIO_BACKEND_SRC}:${PYTHONPATH:-}"

# 可选：指定 workspace root（默认 dev.sh 在 backend/ 下启动，因此无需设置）
# export STUDIO_WORKSPACE_ROOT="${BACKEND_DIR}"

echo "OK: PYTHONPATH configured for Studio MVP"
