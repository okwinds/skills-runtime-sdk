---
name: demo-skill
description: "Minimal example skill for CLI scan and preflight demonstration. Validates that the Skills Runtime SDK toolchain can discover, parse, and preflight-check a SKILL.md file. Use when onboarding new contributors, testing the SDK CLI locally, or verifying a Studio workspace skill directory structure."
metadata:
  version: 0.1.0
---

# Demo Skill

A minimal `SKILL.md` example that exercises the core **Skills Runtime SDK** CLI commands for skill discovery and validation.

## Purpose

This skill exists to verify that the SDK toolchain works end-to-end:

1. **`skills-runtime-sdk skills scan`** — discovers this file in the workspace skill directory.
2. **`skills-runtime-sdk skills preflight`** — validates frontmatter schema, required fields, and file structure.

Use it as a starting template when authoring new skills, or to smoke-test your local SDK installation.

## Workflow

1. Place this `SKILL.md` inside a skill directory (e.g. `skills/demo-skill/SKILL.md`).
2. Run `skills-runtime-sdk skills scan` from the workspace root — confirm the skill appears in the output.
3. Run `skills-runtime-sdk skills preflight` — confirm the skill passes all checks with no errors.
4. Optionally, extend this file with custom sections to prototype a new skill before promoting it to a full implementation.

## Example

```bash
# Scan for skills in the current workspace
skills-runtime-sdk skills scan

# Expected output includes:
#   demo-skill  docs_for_coding_agent/examples/skills/demo-skill/SKILL.md

# Preflight check
skills-runtime-sdk skills preflight

# Expected: all checks pass, no validation errors
```

## Notes

- This skill performs no tool calls and has no runtime side-effects — it is purely a structural example.
- For creating production skills with tool integration, see the `examples/apps/` directory for complete multi-agent workflow patterns.
- You can build richer skill directory structures and content inside Studio.
