---
name: ci-log-analyzer
description: "CI failure analysis skill that reproduces test failures locally and pinpoints the root cause. Uses shell_exec to run pytest (or other test commands) and extracts the minimal set of failing tests with file paths and line numbers. Use when triaging CI pipeline failures, reproducing flaky tests locally, or building automated CI fix workflows."
metadata:
  version: 0.1.0
  short-description: "Analyze: shell_exec(pytest) to reproduce failures"
---

# CI Log Analyzer (App: Analyze Stage)

Reproduces CI failures locally using `shell_exec` and extracts the minimal actionable diagnosis — which file, which line, which function caused the failure. This is the first stage in the `ci_failure_triage_and_fix` workflow.

## Objective

- Reproduce CI test failures locally by running the test suite via `shell_exec`
- Parse test output to identify the exact failing test(s), file paths, and line numbers
- Produce a minimal, actionable diagnosis for the downstream `ci-patcher` skill

## Required Tools

- `shell_exec` — execute test commands (e.g. `pytest`, `npm test`) to reproduce failures

## Workflow

1. **Receive failure context** — accept a CI log snippet, failing job URL, or description of the failure.
2. **Reproduce locally** — use `shell_exec` to run the relevant test command:
   ```bash
   shell_exec("pytest tests/ -x --tb=short")
   ```
3. **Parse test output** — extract from the failure output:
   - Failing test name and file path
   - Line number of the assertion or error
   - Error message and relevant stack trace
   - Whether the failure is deterministic or flaky (run twice if needed)
4. **Produce diagnosis** — output a structured report:
   - File path and line number of the root cause
   - Function or method that needs fixing
   - Minimal description of what went wrong

## Example

```text
Input: "CI is failing on the test suite — reproduce and diagnose"

Step 1 — shell_exec("pytest tests/ -x --tb=short"):
  FAILED tests/test_app.py::test_user_creation - AssertionError:
    assert create_user("") raises ValueError
    but no exception was raised

Output:
  Diagnosis:
    File: app.py:28 — function create_user()
    Issue: missing input validation — empty string not rejected
    Fix target: add `if not username: raise ValueError("username required")`
```

## Notes

- This skill only diagnoses — it does not apply fixes. The downstream `ci-patcher` skill handles patching.
- Part of the `ci_failure_triage_and_fix` app: log-analyzer → patcher → qa-reporter.
