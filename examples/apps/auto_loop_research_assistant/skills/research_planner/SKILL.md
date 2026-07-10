---
name: research-planner
description: "Research planning skill that decomposes user questions into executable steps and tracks progress using update_plan. Breaks complex research queries into a structured sequence of prepare, search, read, and synthesize phases. Use when planning multi-step research workflows, coordinating research agents, or building auto-loop research assistants."
metadata:
  version: 0.1.0
  short-description: "Plan: update_plan for step tracking"
---

# Research Planner (App: Plan Stage)

Decomposes a user's research question into clear, executable steps and uses `update_plan` to track progress through each phase. This is the planning stage in the `auto_loop_research_assistant` workflow.

## Objective

- Break a complex research question into a structured sequence of steps: prepare → search → read → synthesize
- Use `update_plan` to write out the plan and update step status as work progresses
- Produce a clear plan that downstream skills (`research-tool-user`, `research-reporter`) can follow

## Required Tools

- `update_plan` — create and update a structured research plan with step-level status tracking

## Workflow

1. **Receive research question** — accept the user's query or research topic.
2. **Decompose into steps** — break the question into 3–6 concrete, ordered steps:
   - **Prepare**: clarify scope, identify key terms, define success criteria
   - **Search**: list specific sources, databases, or APIs to query
   - **Read**: identify documents or files to analyze in depth
   - **Synthesize**: define the output format (summary, report, recommendation)
3. **Write initial plan** — use `update_plan` to publish the step list with all steps marked as "pending".
4. **Update as work progresses** — after each step completes, call `update_plan` to mark it done and note key findings.

## Example

```text
Input: "Research best practices for Python async error handling"

Step 1 — update_plan:
  Plan:
    1. [pending] Prepare — define scope: async/await patterns, exception propagation, cancellation
    2. [pending] Search — query: Python asyncio exception handling, structured concurrency
    3. [pending] Read — review PEP 654 (ExceptionGroup), asyncio docs, trio patterns
    4. [pending] Synthesize — produce summary with code examples and recommendations

Step 2 — (after search completes) update_plan:
    1. [done] Prepare — scope confirmed
    2. [done] Search — found 5 relevant sources
    3. [in_progress] Read — reviewing PEP 654
    4. [pending] Synthesize
```

## Notes

- This skill only plans and tracks — it does not perform searches or generate reports. The downstream `research-tool-user` executes the plan, and `research-reporter` synthesizes findings.
- Part of the `auto_loop_research_assistant` app: planner → tool-user → reporter.
