<div align="center">

[English](05-skills-guide.md) | [中文](05-skills-guide.cn.md) | [Help](README.md)

</div>

# 05. Skills Guide: syntax, scan, injection, boundaries

## 5.1 The only valid mention format

```text
$[account:domain].skill_name
```

Example:

```text
Please call $[web:mvp].article-writer and generate a ~300-word article.
```

## 5.2 Tolerant extraction vs strict validation (know the difference)

### Free-text extraction

- Extracts **only** valid mentions
- Everything else is treated as plain text (does not interrupt the run)

### Strict tool-argument validation

If a tool argument is typed as `skill_mention`:

- It must be exactly one full mention token
- Invalid formats fail fast with a validation error

## 5.3 Slug rules (simplified)

- Lowercase letters, digits, and hyphens only
- Avoid spaces, special chars, and non-ASCII punctuation

## 5.4 Skill discovery and sources

Supported sources:

- filesystem
- in-memory
- redis
- pgsql

Common combinations:

- dev: filesystem
- prod: filesystem + redis/pgsql (depending on governance requirements)

## 5.5 Scan policy (metadata-only)

Key idea:

- Scanning reads only frontmatter/metadata
- Skill body is loaded lazily during injection

Relevant fields:

- `skills.scan.max_depth`
- `skills.scan.refresh_policy`
- `skills.scan.max_frontmatter_bytes`
- `skills.injection.max_bytes`

## 5.6 Skills CLI practice

### preflight

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills preflight \
  --workspace-root . \
  --config help/examples/skills.cli.overlay.yaml \
  --pretty
```

### scan

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills scan \
  --workspace-root . \
  --config help/examples/skills.cli.overlay.yaml \
  --pretty
```

## 5.7 Create a Skill in Studio (API)

Endpoint: `POST /studio/api/v1/sessions/{session_id}/skills`

Request body:

```json
{
  "name": "article-writer",
  "description": "writing skill",
  "body_markdown": "# Usage\n...",
  "target_root": "<repo_root>/packages/skills-runtime-studio-mvp/backend/.skills_runtime_sdk/skills"
}
```

## 5.8 Common errors

- `target_root must be one of session roots`
- `validation_error` (invalid name)
- duplicate skill name conflicts during scan

## 5.9 Best practices

1. Use consistent naming, avoid duplicates
2. Layer roots by purpose (system/business/experiment)
3. Use preflight/scan as part of CI gates
4. Do not rely on “guessing” mention formats

---

Prev: [04. CLI Reference](04-cli-reference.md) · Next: [06. Tools + Safety](06-tools-and-safety.md)
