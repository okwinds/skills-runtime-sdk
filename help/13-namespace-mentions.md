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

### Characters and casing

Mentions are deliberately strict:
- lowercase only
- predictable slug characters

This prevents accidental look-alikes, and makes it easy to validate mentions before sending them to the runtime.

### Too many segments

Maximum is **7** segments:

- ✅ `$[a0:b1:c2:d3:e4:f5:g6].x`
- ❌ `$[a0:b1:c2:d3:e4:f5:g6:h7].x` (8 segments)

### “Valid token” does not mean “resolves”

Free-text extraction only extracts syntactically valid tokens. Resolution is stricter:

- if a mention refers to a namespace that is not configured/enabled → run fails with `SKILL_SPACE_NOT_CONFIGURED`
- if a mention refers to a skill that is not found in that namespace → run fails with `SKILL_UNKNOWN`

Best practice: treat writing a valid mention token as an explicit request to inject that skill.

### Extraction vs strict validation (know the difference)

- **Free-text extraction** is tolerant: it only extracts valid tokens, and ignores invalid fragments as plain text.
- **Tool arguments** typed as `skill_mention` are strict: the argument value must be **exactly one full token**, otherwise it fails fast.

### A common typo that is intentionally ignored

If the runtime sees a pattern like:

```text
$[web].article-writer]  # stray closing bracket
```

it is intentionally ignored during extraction to reduce “accidental injection” caused by copy/paste bracket mistakes.

## 13.5 Migration note (hard upgrade)

This is a **breaking change** by design:

- Legacy two-part mention tokens are not supported.
- Legacy config fields for the space key are rejected; use `skills.spaces[].namespace` instead.

See also:
- Mention basics: `help/05-skills-guide.md`
- Config schema: `help/02-config-reference.md`
- Troubleshooting: `help/09-troubleshooting.md`

## 13.6 Scenario cookbook (examples + combinations)

### 13.6.1 Single mention in plain text

```text
Please follow $[web].article-writer and produce a draft.
```

Expected behavior:
- the mention is extracted
- the skill body is injected once (even if it appears multiple times)

### 13.6.2 Multiple mentions in one request (order matters)

```text
Use $[web].outline-generator then apply $[web].article-writer.
```

Expected behavior:
- extracted order is preserved
- injection order follows first appearance in text

### 13.6.3 Duplicate mentions are deduplicated (first wins)

```text
$[web].article-writer … later again … $[web].article-writer
```

Expected behavior:
- injected once
- later duplicates do not re-inject the body (reduces token waste)

### 13.6.4 Same `skill_name`, different namespaces (explicit is better)

```text
$[docs].reviewer
$[code].reviewer
```

Expected behavior:
- both are distinct, because the key is `(namespace, skill_name)`
- this is the intended way to avoid “global name collisions”

### 13.6.5 Mentions inside Markdown are still mentions

Mentions are extracted from raw text, so these still count:

```md
- Do A with $[web].article-writer
- Do B with $[web:mvp].sanity-checker
```

Best practice (UX): if your product shows documentation, avoid displaying *valid* mention tokens unless you expect users to trigger injection by copy/paste.

### 13.6.6 How to talk about mentions without triggering injection (escaping)

If you need to include a token-like string as documentation, avoid the exact `$[...].name` sequence. Options:

```text
$ [web].article-writer          # add a space after `$`
$[web] .article-writer          # add a space before `.`
$[web]\\.article-writer         # escape the dot
$[WEB].article-writer           # uppercase makes it invalid (if acceptable)
```

Note: wrapping with backticks (`` `$[web].x` ``) does not prevent extraction, because extraction scans raw text.

### 13.6.7 “Looks valid” but fails at runtime: space not configured

```text
$[acme:secret].article-writer
```

If `skills.spaces[]` does not include `namespace=acme:secret` (or the space is disabled), resolution fails.

Fix:
- configure the space and sources (`skills.spaces[]` + `skills.sources[]`)
- or change the mention to a configured namespace

### 13.6.8 “Looks valid” but fails at runtime: skill not found

```text
$[web].does-not-exist
```

Even though the token is valid, the scan index may not contain that `(namespace, skill_name)`, so resolution fails.

Fix:
- ensure the skill exists in a source reachable by that namespace
- run a skills scan (or use `refresh_policy=always` during development)

## 13.7 Best practices (security + UX)

### 13.7.1 Namespace design

- Treat namespaces as stable, human-readable paths (order-sensitive).
- Prefer “ownership/structure” segments (org/team/project) over ephemeral ones.
- Keep to the minimum depth needed; don’t encode secrets or sensitive identifiers.

### 13.7.2 Product/UI ergonomics

- Provide mention autocomplete and validation (avoid manual typing).
- If a user enters an unknown mention, show a clear error *before* running the agent.
- Display the “available skills list” (or a filtered picker) so users don’t guess names.

### 13.7.3 Token budget / injection hygiene

- Keep skill bodies focused and composable.
- Prefer a few targeted skills over injecting many large skills at once.
- Set and monitor `skills.injection.max_bytes` to avoid unexpected prompt bloat.
