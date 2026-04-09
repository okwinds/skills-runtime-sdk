---
name: repo-analyzer
description: "Repository analysis skill for multi-agent code change pipelines. Reads key source files using read_file to locate bugs, anti-patterns, or improvement targets, then outputs a structured diagnosis with minimal fix suggestions. Use when triaging repository issues, starting a code change pipeline, or performing automated code review analysis."
metadata:
  version: 0.1.0
  short-description: "read_file → diagnosis"
---

# Repo Analyzer (Pipeline: Analyze Stage)

Reads key repository files and produces a structured diagnosis identifying problems and minimal fix suggestions. This is the first stage in the `repo_change_pipeline_pro` multi-agent workflow.

## Objective

- Read critical source files (e.g. `app.py`, `test_app.py`) using `read_file`
- Identify bugs, failing tests, anti-patterns, or improvement targets
- Output a structured diagnosis with minimal, actionable fix suggestions

## Required Tools

- `read_file` — read source files from the repository workspace

## Workflow

1. **Receive the analysis request** — accept a target file list or scan the workspace for key entry points.
2. **Read source files** — use `read_file` on each target (e.g. `app.py`, `test_app.py`, configuration files).
3. **Analyze content** — identify issues such as:
   - Failing test assertions or missing test coverage
   - Logic errors or off-by-one bugs
   - Import errors or missing dependencies
   - Anti-patterns (hardcoded secrets, unhandled exceptions)
4. **Produce diagnosis** — output a structured report containing:
   - File path and line number of each issue
   - Issue category (bug, anti-pattern, missing test, etc.)
   - Minimal fix suggestion (what to change, not a full patch)

## Example

```text
Input:  "Analyze app.py and test_app.py for issues"

Output:
  Diagnosis:
    1. app.py:42 — BUG — division by zero when `count` is 0
       Fix: add guard `if count == 0: return default_value`
    2. test_app.py:15 — MISSING_COVERAGE — no test for edge case count=0
       Fix: add test_divide_by_zero test case
```

## Notes

- This skill only reads files and produces analysis — it does not modify code. The downstream `repo-patcher` skill applies fixes.
- Part of the `repo_change_pipeline_pro` app: analyzer → patcher → qa-runner → reporter.
