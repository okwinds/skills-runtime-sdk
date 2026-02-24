<div align="center">

[English](11-faq.md) | [中文](11-faq.cn.md) | [Help](README.md)

</div>

# 11. FAQ

## Q1: Why doesn’t `$skill_name` work?

A: The supported syntax is `$[account:domain].skill_name`. `$skill_name` is not a mention token and is ignored in free text.

## Q2: Why don’t invalid mentions throw an error?

A: This is intentional to avoid interrupting the conversation. In free text, mention extraction is tolerant: invalid fragments are treated as normal text. Strict errors only happen at the tool-parameter layer.

## Q3: Is sandbox enabled by default in the SDK?

A: The SDK default is `sandbox.default_policy=none` (it does not force OS sandbox by default to avoid changing behavior).

Studio MVP’s example overlay (`packages/skills-runtime-studio-mvp/backend/config/runtime.yaml.example`) sets `sandbox.default_policy` to `restricted` by default, so when you run the MVP you’re more likely to notice sandbox-related constraints.

To turn “feels like sandbox” into hard evidence, run:

```bash
bash scripts/integration/os_sandbox_restriction_demo.sh
```

## Q4: What’s the difference between approvals and sandbox?

A: Approvals answer “should we do it?”, sandbox answers “even if allowed, how far can it go?”. They complement each other.

## Q5: When `sandbox_denied` happens, does it auto-downgrade to no-sandbox?

A: No. The semantics are explicit denial to avoid silent “looks safe but actually runs unrestricted” downgrades.

## Q6: Can I disable all approvals?

A: You can set `safety.mode=allow`, but it is not recommended. At minimum keep a denylist to reduce accidental destructive actions.

## Q7: How do I troubleshoot a stuck Studio run?

A: Check whether `events.jsonl` is still being appended, then check pending approvals, then verify LLM/overlay/env config paths.

## Q8: How do I know where a config field came from?

A: Use bootstrap’s config source tracking (`resolve_effective_run_config(...).sources`), or check the Studio Info Dock `Config` tab (`run_started.config_summary`).

## Q9: Why does something work locally but fail in production?

A: Common causes: missing `bwrap` on Linux, different filesystem permissions, or overlay/env drift. Follow `help/09-troubleshooting.md` step by step.

## Q10: If I want to extend the system, where should I start reading?

A: Recommended order:

1. `help/08-architecture-internals.md`
2. `help/06-tools-and-safety.md` (mental model + observability fields)
3. Code under `packages/skills-runtime-sdk-python/src/agent_sdk/*` (start at `agent_sdk/core/agent.py` and `agent_sdk/tools/builtin/*`)

---

Prev: [`10-cookbook.md`](./10-cookbook.md)
