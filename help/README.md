<div align="center">

[English](README.md) | [中文](README.cn.md)

</div>

# Skills Runtime SDK / Studio — Help Index

> This is a **hands-on** guide. The goal is not to explain concepts in the abstract, but to help you **run it, debug it, and operate it safely**.

## Who this is for

- Product/feature engineers integrating the SDK into an existing app
- Platform engineers extending tools / skills / approvals / sandbox
- QA / Ops engineers defining regression and troubleshooting playbooks

## Recommended reading order

1. `help/00-overview.md`: mental model, boundaries, terminology
2. `help/01-quickstart.md`: get a minimal end-to-end flow working
3. `help/02-config-reference.md`: config precedence and safe defaults
4. `help/04-cli-reference.md`: CLI commands and exit codes
5. `help/06-tools-and-safety.md`: tools, approvals, sandbox, exec sessions
6. `help/14-safety-deep-dive.md`: gatekeeper vs fence (practical patterns + pitfalls)
7. `help/07-studio-guide.md`: Studio MVP backend/frontend/API flow
8. `help/09-troubleshooting.md`: symptom → root cause → fix

Chinese counterparts:
- `help/00-overview.cn.md`
- `help/01-quickstart.cn.md`
- `help/02-config-reference.cn.md`
- `help/04-cli-reference.cn.md`
- `help/06-tools-and-safety.cn.md`
- `help/14-safety-deep-dive.cn.md`
- `help/07-studio-guide.cn.md`
- `help/09-troubleshooting.cn.md`

## Document map

- 00 Overview: `help/00-overview.md` ｜ 中文：`help/00-overview.cn.md`
- 01 Quickstart: `help/01-quickstart.md` ｜ 中文：`help/01-quickstart.cn.md`
- 02 Config Reference: `help/02-config-reference.md` ｜ 中文：`help/02-config-reference.cn.md`
- 03 Python API: `help/03-sdk-python-api.md` ｜ 中文：`help/03-sdk-python-api.cn.md`
- 04 CLI Reference: `help/04-cli-reference.md` ｜ 中文：`help/04-cli-reference.cn.md`
- 05 Skills Guide: `help/05-skills-guide.md` ｜ 中文：`help/05-skills-guide.cn.md`
- 06 Tools + Safety: `help/06-tools-and-safety.md` ｜ 中文：`help/06-tools-and-safety.cn.md`
- 07 Studio Guide: `help/07-studio-guide.md` ｜ 中文：`help/07-studio-guide.cn.md`
- 08 Architecture Internals: `help/08-architecture-internals.md` ｜ 中文：`help/08-architecture-internals.cn.md`
- 09 Troubleshooting: `help/09-troubleshooting.md` ｜ 中文：`help/09-troubleshooting.cn.md`
- 10 Cookbook: `help/10-cookbook.md` ｜ 中文：`help/10-cookbook.cn.md`
- 11 FAQ: `help/11-faq.md` ｜ 中文：`help/11-faq.cn.md`
- 12 Validation suites: `help/12-validation-suites.md` ｜ 中文：`help/12-validation-suites.cn.md`
- 13 Namespace mentions: `help/13-namespace-mentions.md` ｜ 中文：`help/13-namespace-mentions.cn.md`
- 14 Safety deep dive: `help/14-safety-deep-dive.md` ｜ 中文：`help/14-safety-deep-dive.cn.md`

Supplemental topics (not numbered):
- Sandbox best practices: `help/sandbox-best-practices.md` ｜ 中文：`help/sandbox-best-practices.cn.md`

## Examples

- `help/examples/sdk.overlay.yaml`: generic SDK overlay example
- `help/examples/skills.cli.overlay.yaml`: minimal Skills CLI overlay
- `help/examples/studio.runtime.overlay.yaml`: Studio balanced-mode overlay example
- `help/examples/run_agent_minimal.py`: minimal Python run example
- `help/examples/run_agent_with_custom_tool.py`: custom tool example
- `help/examples/studio-api.http`: Studio API call examples
- `examples/`: full-spectrum offline examples library (step_by_step/tools/skills/state)
- `docs_for_coding_agent/`: coding-agent teaching pack (CAP inventory + coverage map + cheatsheets)

## Notes / constraints

- Python `>=3.10` is required (`packages/skills-runtime-sdk-python/pyproject.toml`)
- Use `<repo_root>` placeholders in docs (avoid machine-specific absolute paths)
- Never commit secrets: only `.env.example` goes into the repo, real keys stay in local `.env`
- Treat Help as the operational handbook; for deeper details, read code under `packages/skills-runtime-sdk-python/src/skills_runtime/*`
