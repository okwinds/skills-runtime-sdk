<div align="center">

[English](13-namespace-mentions.md) | [中文](13-namespace-mentions.cn.md) | [Help](README.md)

</div>

# 13. Namespace Mentions: neutral, ordered, multi-segment

This framework deliberately treats the "Skill space key" as a **neutral namespace**, not as business-specific labels.

## 13.1 The only valid mention token format

```text
$[<namespace>].<skill_name>
```

Where:

- `<namespace>` MUST be `1..7` **ordered** segments separated by `:` (example: `org:team:project`)
- Each segment MUST be a slug: lowercase letters / digits / hyphens only, starts & ends with `[a-z0-9]`, length `2..64`
- `<skill_name>` keeps the existing slug rules: lowercase letters / digits / hyphens / underscores, starts & ends with `[a-z0-9]`, length `2..64`

Examples:

```text
$[web].article-writer
$[web:mvp].article-writer
$[acme:platform:runtime].article-writer
```

## 13.2 Why "namespace" (and why not a fixed two-part label)

Historically, using a fixed two-part label feels convenient, but it unintentionally narrows the mental model:

- Many real keys are `parent:child`, or `org:project`, or `team:service`, etc.
- A fixed 2-segment model becomes a structural bottleneck when you grow from 1 → 2 → 3 segments.

So the framework upgrades the contract to:

- **Neutral naming**: `namespace` + `segment`
- **Variable length**: `1..7` segments
- **Order-sensitive**: the sequence matters

## 13.3 Order-sensitive semantics (best practice)

`namespace` is treated as an **ordered path**:

```text
$[a:b].x  !=  $[b:a].x
```

Rationale (framework-level):

- Order-insensitive canonicalization (sorting segments) introduces ambiguity and hides conflicts.
- The runtime should be auditable and predictable; "guessing" belongs in product/UI.

Best practice:

- Choose a stable ordering convention early (example only): `org:team:project[:env][:region]...`
- Keep it human-readable; do not encode business semantics into the runtime contract unless your product layer needs it.

## 13.4 Common pitfalls

### Segment length

Segment minimum length is **2**:

- ✅ `web`
- ❌ `a`

### Too many segments

Maximum is **7** segments:

- ✅ `$[a0:b1:c2:d3:e4:f5:g6].x`
- ❌ `$[a0:b1:c2:d3:e4:f5:g6:h7].x` (8 segments)

### Extraction vs strict validation (know the difference)

- **Free-text extraction** is tolerant: it only extracts valid tokens, and ignores invalid fragments as plain text.
- **Tool arguments** typed as `skill_mention` are strict: the argument value must be **exactly one full token**, otherwise it fails fast.

## 13.5 Migration note (hard upgrade)

This is a **breaking change** by design:

- Legacy two-part mention tokens are not supported.
- Legacy config fields for the space key are rejected; use `skills.spaces[].namespace` instead.

See also:
- Mention basics: `help/05-skills-guide.md`
- Config schema: `help/02-config-reference.md`
- Troubleshooting: `help/09-troubleshooting.md`
