#!/usr/bin/env bash
set -euo pipefail

export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8
export PYTHONUTF8=1

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
    echo "WARN: ${var_name} points to missing env file; unsetting to avoid test failure." >&2
    echo "  ${var_name}=${raw_val}" >&2
    echo "  resolved=${resolved}" >&2
    unset "${var_name}"
  fi
}

# 避免遗留 shell 环境变量指向旧/不存在路径导致 import 即崩溃。
sanitize_env_file_var "SKILLS_RUNTIME_SDK_ENV_FILE"
sanitize_env_file_var "AGENT_SDK_ENV_FILE"

sanitize_overlay_paths_var() {
  local var_name="$1"
  local raw_val="${!var_name:-}"
  if [[ -z "${raw_val}" ]]; then
    return 0
  fi

  local normalized
  normalized="$(printf '%s' "${raw_val}" | tr ';' ',')"

  local IFS=','
  local parts=()
  read -r -a parts <<<"${normalized}"

  local kept=()
  local dropped=()
  for p in "${parts[@]}"; do
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
    echo "WARN: ${var_name} contains missing overlay config path(s); dropping to avoid test failure." >&2
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

sanitize_overlay_paths_var "SKILLS_RUNTIME_SDK_CONFIG_PATHS"
sanitize_overlay_paths_var "AGENT_SDK_CONFIG_PATHS"

pytest -q
