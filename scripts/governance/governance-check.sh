#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

FULL=0
WRITE_REPORT=0
REQUIRE_LOCAL_DOCS="${REQUIRE_LOCAL_DOCS:-0}"

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

push_issue() {
  local level="$1"
  local message="$2"

  case "${level}" in
    pass)
      PASSES+=("${message}")
      ;;
    warn)
      WARNS+=("${message}")
      ;;
    error)
      ERRORS+=("${message}")
      ;;
    *)
      echo "unknown issue level: ${level}" >&2
      exit 2
      ;;
  esac
}

has_fixed_string() {
  local pattern="$1"
  local path="$2"

  if command -v rg >/dev/null 2>&1; then
    rg -q --fixed-strings "${pattern}" "${path}"
    return $?
  fi

  grep -Fq -- "${pattern}" "${path}"
}

check_exists() {
  local path="$1"
  local label="$2"
  local missing_level="${3:-error}"

  if [[ -e "${path}" ]]; then
    push_issue "pass" "${label}: present"
  else
    push_issue "${missing_level}" "${label}: missing (${path#${REPO_ROOT}/})"
  fi
}

check_contains() {
  local path="$1"
  local pattern="$2"
  local label="$3"
  local missing_level="${4:-error}"

  if [[ ! -f "${path}" ]]; then
    push_issue "${missing_level}" "${label}: missing (${path#${REPO_ROOT}/})"
    return 0
  fi

  if has_fixed_string "${pattern}" "${path}"; then
    push_issue "pass" "${label}: indexed"
  else
    push_issue "${missing_level}" "${label}: missing index entry '${pattern}' in ${path#${REPO_ROOT}/}"
  fi
}

check_exists "${REPO_ROOT}/scripts/governance/governance-check.sh" "governance-script"
check_exists "${REPO_ROOT}/README.md" "repo-readme"
check_exists "${REPO_ROOT}/docs_for_coding_agent/DOCS_INDEX.md" "public-docs-index"

if [[ "${REQUIRE_LOCAL_DOCS}" == "1" ]]; then
  push_issue "pass" "local-docs-mode: enforced"
  check_exists "${REPO_ROOT}/docs/policies/governance-gate.md" "governance-policy"
  check_exists "${REPO_ROOT}/docs/worklog.md" "worklog"
  check_contains "${REPO_ROOT}/DOCS_INDEX.md" "docs/policies/governance-gate.md" "docs-index-governance-gate"
else
  push_issue "pass" "local-docs-mode: optional"
  check_exists "${REPO_ROOT}/docs/policies/governance-gate.md" "governance-policy" "warn"
  check_exists "${REPO_ROOT}/docs/worklog.md" "worklog" "warn"
  check_contains "${REPO_ROOT}/DOCS_INDEX.md" "docs/policies/governance-gate.md" "docs-index-governance-gate" "warn"
fi

if [[ "${FULL}" -eq 1 ]]; then
  if [[ "${REQUIRE_LOCAL_DOCS}" == "1" ]]; then
    if [[ ! -s "${REPO_ROOT}/docs/task-summaries/INDEX.md" ]]; then
      WARNS+=("task-summaries-index: missing or empty")
    else
      PASSES+=("task-summaries-index: present")
    fi
  else
    WARNS+=("task-summaries-index: skipped in public checkout")
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
