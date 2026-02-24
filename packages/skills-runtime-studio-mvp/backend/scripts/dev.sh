#!/usr/bin/env bash
set -euo pipefail

export LC_ALL="${LC_ALL:-en_US.UTF-8}"
export LANG="${LANG:-en_US.UTF-8}"
export PYTHONUTF8="${PYTHONUTF8:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../" && pwd)"

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

cd "${BACKEND_DIR}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

sanitize_env_file_var() {
  local var_name="$1"
  local raw_val="${!var_name:-}"
  if [[ -z "${raw_val}" ]]; then
    return 0
  fi

  local resolved="${raw_val}"
  if [[ "${resolved}" != /* ]]; then
    resolved="${BACKEND_DIR}/${resolved}"
  fi

  if [[ ! -f "${resolved}" ]]; then
    echo "WARN: ${var_name} points to missing env file; unsetting to avoid startup failure." >&2
    echo "  ${var_name}=${raw_val}" >&2
    echo "  resolved=${resolved}" >&2
    unset "${var_name}"
  fi
}

# 避免用户 shell 环境里遗留的旧路径（例如 legacy 目录）导致启动直接失败。
sanitize_env_file_var "SKILLS_RUNTIME_SDK_ENV_FILE"

sanitize_overlay_paths_var() {
  local var_name="$1"
  local raw_val="${!var_name:-}"
  if [[ -z "${raw_val}" ]]; then
    return 0
  fi

  # SDK bootstrap 支持逗号/分号分隔；相对路径以 workspace_root（此处为 backend/）为锚点。
  local normalized
  normalized="$(printf '%s' "${raw_val}" | tr ';' ',')"

  local IFS=','
  local parts=()
  read -r -a parts <<<"${normalized}"

  local kept=()
  local dropped=()
  for p in "${parts[@]}"; do
    # trim
    p="${p#"${p%%[![:space:]]*}"}"
    p="${p%"${p##*[![:space:]]}"}"
    if [[ -z "${p}" ]]; then
      continue
    fi

    local resolved="${p}"
    if [[ "${resolved}" != /* ]]; then
      resolved="${BACKEND_DIR}/${resolved}"
    fi

    if [[ -f "${resolved}" ]]; then
      kept+=("${p}")
    else
      dropped+=("${p}")
    fi
  done

  if [[ "${#dropped[@]}" -gt 0 ]]; then
    echo "WARN: ${var_name} contains missing overlay config path(s); dropping to avoid runtime failure." >&2
    echo "  dropped: ${dropped[*]}" >&2
  fi

  if [[ "${#kept[@]}" -eq 0 ]]; then
    unset "${var_name}"
    return 0
  fi

  local joined=""
  for k in "${kept[@]}"; do
    if [[ -z "${joined}" ]]; then
      joined="${k}"
    else
      joined="${joined},${k}"
    fi
  done
  export "${var_name}=${joined}"
}

# 避免用户 shell 环境里遗留的旧 web-mvp overlay 路径导致运行时报错。
sanitize_overlay_paths_var "SKILLS_RUNTIME_SDK_CONFIG_PATHS"

python -m uvicorn studio_api.app:app --reload --host "${HOST}" --port "${PORT}"
