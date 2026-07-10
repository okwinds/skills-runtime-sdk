---
name: incident-triager
description: "Incident triage skill for interactive troubleshooting workflows. Reads incident logs using read_file and asks targeted clarification questions via request_user_input to narrow down root cause. Use when triaging production incidents, performing interactive log analysis, or building human-in-the-loop diagnostic agents."
metadata:
  version: 0.1.0
  short-description: "Triage: read_file + request_user_input"
---

# Incident Triager (App: Triage Stage)

Reads incident logs and interactively asks the user targeted clarification questions to narrow down the root cause. This skill demonstrates human-in-the-loop triage using `read_file` and `request_user_input`.

## Objective

- Read incident log files (e.g. `incident.log`) using `read_file`
- Analyze log entries for error patterns, timestamps, and severity indicators
- Use `request_user_input` to ask 2–3 targeted clarification questions that help narrow the root cause

## Required Tools

- `read_file` — read incident logs and related configuration files
- `request_user_input` — ask the user targeted clarification questions

## Workflow

1. **Receive triage request** — accept an incident ID, log file path, or description of the issue.
2. **Read incident logs** — use `read_file` to load `incident.log` or the specified log file.
3. **Analyze log content** — identify:
   - Error messages and stack traces
   - Timestamps and event sequences
   - Affected services or components
   - Severity levels and error frequencies
4. **Formulate clarification questions** — based on gaps in the log data, ask the user 2–3 questions via `request_user_input`, such as:
   - "When did you first notice the issue?"
   - "Was there a recent deployment or config change?"
   - "Which downstream services are affected?"
5. **Produce triage summary** — combine log analysis with user answers into a structured triage report for the downstream `runbook-writer` or `incident-reporter` skills.

## Example

```text
Input: "Triage the incident in incident.log"

Step 1 — read_file("incident.log"):
  [ERROR 2024-03-15T14:22:01] Connection refused: db-primary:5432
  [WARN  2024-03-15T14:22:03] Failover to db-replica triggered

Step 2 — request_user_input:
  Q1: "Was there scheduled maintenance on db-primary around 14:20 UTC?"
  Q2: "Are other services connecting to db-primary also affected?"

Output:
  Triage: Database primary connection failure at 14:22 UTC.
  Likely cause: unplanned db-primary outage (user confirmed no scheduled maintenance).
  Next step: check db-primary host health, review failover logs.
```

## Notes

- This skill demonstrates the human-in-the-loop pattern — it pauses execution to gather user context before concluding.
- Part of the `incident_triage_assistant` app: triager → runbook-writer → incident-reporter.
