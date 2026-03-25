#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

FULL=0
WRITE_REPORT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full)
      FULL=1
      shift
      ;;
    --report)
      WRITE_REPORT=1
      shift
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

_now() {
  date '+%Y-%m-%d %H:%M:%S %z'
}

REPORT_DIR="${REPO_ROOT}/logs/governance"
REPORT_PATH="${REPORT_DIR}/latest-report.md"

declare -a PASSES=()
declare -a WARNS=()
declare -a ERRORS=()

check_exists() {
  local path="$1"
  local label="$2"
  if [[ -e "${path}" ]]; then
    PASSES+=("${label}: present")
  else
    ERRORS+=("${label}: missing (${path#${REPO_ROOT}/})")
  fi
}

check_contains() {
  local path="$1"
  local pattern="$2"
  local label="$3"
  if rg -q --fixed-strings "${pattern}" "${path}"; then
    PASSES+=("${label}: indexed")
  else
    ERRORS+=("${label}: missing index entry '${pattern}' in ${path#${REPO_ROOT}/}")
  fi
}

check_exists "${REPO_ROOT}/docs/policies/governance-gate.md" "governance-policy"
check_exists "${REPO_ROOT}/scripts/governance/governance-check.sh" "governance-script"
check_exists "${REPO_ROOT}/docs/worklog.md" "worklog"
check_contains "${REPO_ROOT}/DOCS_INDEX.md" "docs/policies/governance-gate.md" "docs-index-governance-gate"

if [[ "${FULL}" -eq 1 ]]; then
  if [[ ! -s "${REPO_ROOT}/docs/task-summaries/INDEX.md" ]]; then
    WARNS+=("task-summaries-index: missing or empty")
  else
    PASSES+=("task-summaries-index: present")
  fi
fi

SUMMARY="PASS"
if [[ "${#ERRORS[@]}" -gt 0 ]]; then
  SUMMARY="ERROR"
elif [[ "${#WARNS[@]}" -gt 0 ]]; then
  SUMMARY="WARN"
fi

if [[ "${WRITE_REPORT}" -eq 1 ]]; then
  mkdir -p "${REPORT_DIR}"
  {
    echo "# Governance Report"
    echo
    echo "Summary: ${SUMMARY}"
    echo "Generated At: $(_now)"
    echo
    echo "## Pass"
    if [[ "${#PASSES[@]}" -eq 0 ]]; then
      echo "- none"
    else
      for item in "${PASSES[@]}"; do
        echo "- ${item}"
      done
    fi
    echo
    echo "## Warn"
    if [[ "${#WARNS[@]}" -eq 0 ]]; then
      echo "- none"
    else
      for item in "${WARNS[@]}"; do
        echo "- ${item}"
      done
    fi
    echo
    echo "## Error"
    if [[ "${#ERRORS[@]}" -eq 0 ]]; then
      echo "- none"
    else
      for item in "${ERRORS[@]}"; do
        echo "- ${item}"
      done
    fi
  } > "${REPORT_PATH}"
fi

echo "Summary: ${SUMMARY}"
if [[ "${#ERRORS[@]}" -gt 0 ]]; then
  exit 1
fi
exit 0
