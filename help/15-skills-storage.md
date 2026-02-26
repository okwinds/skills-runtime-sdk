<div align="center">

[English](15-skills-storage.md) | [中文](15-skills-storage.cn.md) | [Help](README.md)

</div>

# 15. Skills storage: filesystem + Redis + PostgreSQL

This chapter answers two practical questions:

1) **Where do Skills live on disk in a workspace?** (Studio & local dev)
2) **How do I operate Skills as a production-grade store?** (Redis/PGSQL sources, governance, failure modes)

It is intentionally **hands-on** and aligned with the current implementation.

See also:
- Skills basics (mentions/scan/injection): `help/05-skills-guide.md`
- Config schema (spaces/sources/scan/injection): `help/02-config-reference.md`
- Internals (scan vs inject, sources map): `help/08-architecture-internals.md`

## 15.1 Mental model: spaces, sources, scan, inject

Skills storage is configured by two lists:

- `skills.spaces[]`: **namespaces** that are enabled, and which sources they read from
- `skills.sources[]`: **storage backends** and their connection/options

```text
mention token: $[namespace].skill_name
            │
            v
resolve(namespace, skill_name)
  │
  ├─ validate: namespace is configured/enabled
  ├─ scan(): build index from sources (metadata-only)
  └─ inject(): lazy-load body via body_loader (budgeted)
```

Key property:
- **Scan is metadata-only** (fast, cheap).
- **Injection loads bodies** (controlled by `skills.injection.max_bytes`).

### 15.1.1 `resolve_mentions` end-to-end flow (discovery vs injection vs tools)

This diagram makes the boundaries explicit between config-driven discovery (spaces/sources), body injection, and Phase 3 assets (actions/references):

```text
user_task_text
  │
  ├─ SkillsManager.resolve_mentions(text)
  │    ├─ extract_skill_mentions()                         # parse $[namespace].skill_name tokens
  │    ├─ scan()                                           # refresh per skills.scan.refresh_policy
  │    │    ├─ filesystem / redis / pgsql / in-memory          # metadata-only (no body, no bundle)
  │    │    └─ build index: (namespace, skill_name) → Skill
  │    └─ map mentions → Skill objects
  │
  ├─ Prompt injection (Agent loop)
  │    └─ SkillsManager.render_injected_skill(skill)
  │         └─ skill.body_loader()                         # lazy body load (budgeted by skills.injection.max_bytes)
  │
  └─ Phase 3 tools (only when invoked)
       ├─ skill_exec / skill_ref_read
       │    └─ (Redis) lazy fetch + safe extract bundle        # tools only; scan/inject never read bundles
       └─ shell_exec / restricted file reads ...
            └─ approvals gate + OS sandbox fence
```

## 15.2 A) Filesystem storage (workspace-friendly)

### 15.2.1 Where Studio stores skills by default

Studio APIs commonly write new skills under:

```text
<workspace_root>/.skills_runtime_sdk/skills/
```

Example (one possible layout):

```text
<workspace_root>/.skills_runtime_sdk/skills/
  web/
    article-writer/
      SKILL.md
  code/
    reviewer/
      SKILL.md
```

Notes:
- The scanner looks for files named exactly `SKILL.md` under the configured root (recursive).
- The directory structure is **your choice**; the framework does not require a fixed nesting scheme.

### 15.2.2 Filesystem source config (minimal)

```yaml
config_version: 1

skills:
  spaces:
    - id: web
      namespace: web
      sources: [fs_ws]
      enabled: true

  sources:
    - id: fs_ws
      type: filesystem
      options:
        # Relative paths are resolved under workspace_root.
        root: .skills_runtime_sdk/skills
```

### 15.2.3 Multiple filesystem roots (governance)

A common production/dev pattern is “layered roots”:

```yaml
skills:
  spaces:
    - id: web
      namespace: web
      sources: [fs_system, fs_business]
  sources:
    - id: fs_system
      type: filesystem
      options: { root: skills/system }
    - id: fs_business
      type: filesystem
      options: { root: skills/business }
```

Benefits:
- “system skills” can be reviewed/locked down separately
- “business skills” can change more frequently
- you can make sources read-only at the filesystem level

### 15.2.4 Operational best practices (filesystem)

- Treat `.skills_runtime_sdk/` as **runtime-owned artifacts** (safe to delete and rebuild).
- For reproducible deployments, store curated skills in a repo directory (e.g., `skills/`) and use filesystem sources pointing there.
- Keep skill bodies small and composable; monitor `skills.injection.max_bytes`.

## 15.3 B) Production-grade sources: Redis and PostgreSQL

The SDK supports scanning from remote sources:

- `redis`: metadata in hashes + body in a separate key
- `pgsql`: metadata + body in a table

### 15.3.1 Shared rules and governance

#### Required options (preflight)

The Skills config preflight enforces required `source.options` per type:

```text
filesystem: root
in-memory:  namespace
redis:      dsn_env + key_prefix
pgsql:      dsn_env + schema + table
```

#### DSN via environment variable (no secrets in YAML)

Remote sources use `dsn_env` and read DSN from the process environment.

```yaml
skills:
  sources:
    - id: redis_main
      type: redis
      options:
        dsn_env: SKILLS_REDIS_DSN
        key_prefix: "skills:"
```

This avoids storing credentials in overlay YAML or in the repository.

#### Dependencies are optional (fail with structured errors)

- Redis source requires `redis` (Python dependency).
- PGSQL source requires `psycopg`.

If the dependency is missing or the connection/query fails, scan returns a structured source-unavailable error.

### 15.3.2 Redis source: data contract (how keys are shaped)

Config:

```yaml
skills:
  spaces:
    - id: web
      namespace: web
      sources: [redis_main]
  sources:
    - id: redis_main
      type: redis
      options:
        dsn_env: SKILLS_REDIS_DSN
        key_prefix: "skills:"
```

Scan pattern (metadata-only):

```text
{key_prefix}meta:{namespace}:*
```

Each matched key is a Redis hash whose fields follow a minimal contract:

```text
skill_name        string   (required; slug)
description       string   (required)
created_at        string   (required; RFC3339 recommended)
required_env_vars string   (optional; JSON string: ["ENV_A","ENV_B"])
metadata          string   (optional; JSON string: {"k":"v"})
body_key          string   (optional)
body_size         int      (optional)
etag             string   (optional)
updated_at        string   (optional)
scope            string   (optional)
```

#### Redis bundles (Phase 3: minimal asset protocol for actions / references)

If you want Redis skills to support Phase 3 tools (`skill_exec` / `skill_ref_read`), you need an additional **zip bundle**. The minimal format only allows `actions/` + `references/`.

Important constraints:
- `scan()` stays metadata-only: it MUST NOT read bundle bytes during scanning.
- Bundle bytes are fetched only on the tool execution path (`skill_exec` / `skill_ref_read`); missing/invalid bundles MUST fail closed (no command execution, no reference reads).

Additional metadata fields (stored in the meta hash):

```text
bundle_sha256   string   (strongly recommended; cache/audit/TOCTOU safety)
bundle_key      string   (optional; override the default key)
bundle_size     int      (optional; budgets/observability)
bundle_format   string   (must be "zip"; other values are invalid)
```

Default bundle bytes key (when `bundle_key` is missing):

```text
{key_prefix}bundle:{namespace}:{skill_name}
```

If `body_key` is not present, it defaults to:

```text
{key_prefix}body:{namespace}:{skill_name}
```

Injection (body load) reads the body via `GET <body_key>` and expects UTF-8 bytes or a string.

Recommended practice:
- store `required_env_vars` and `metadata` as **JSON strings** (not native Redis types)
- treat `key_prefix` as a namespace boundary for access control and cleanup

### 15.3.3 PostgreSQL source: table contract

Config:

```yaml
skills:
  spaces:
    - id: web
      namespace: web
      sources: [pg_main]
  sources:
    - id: pg_main
      type: pgsql
      options:
        dsn_env: SKILLS_PG_DSN
        schema: public
        table: skills
```

Scan query (metadata-only) expects:

```text
SELECT id, namespace, skill_name, description, body_size, body_etag, created_at, updated_at,
       required_env_vars, metadata, scope
FROM "<schema>"."<table>"
WHERE enabled = TRUE AND namespace = %s
```

Body load (lazy injection) reads:

```text
SELECT body FROM "<schema>"."<table>" WHERE id = %s AND namespace = %s
```

#### Minimal DDL (recommended)

```sql
CREATE TABLE public.skills (
  id               uuid PRIMARY KEY,
  namespace        text NOT NULL,
  skill_name       text NOT NULL,
  description      text NOT NULL,
  body             text NOT NULL,
  enabled          boolean NOT NULL DEFAULT true,
  scope            text NULL,
  required_env_vars text[] NOT NULL DEFAULT '{}'::text[],
  metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
  body_size        integer NULL,
  body_etag        text NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NULL
);

CREATE INDEX skills_namespace_enabled_idx ON public.skills(namespace, enabled);
CREATE UNIQUE INDEX skills_namespace_name_uq ON public.skills(namespace, skill_name);
```

Notes:
- `required_env_vars` should be `text[]` (the runtime expects a list of strings).
- `metadata` should be `jsonb` (the runtime expects a dict-like object).
- `schema` and `table` identifiers are validated to a safe regex by the runtime (to avoid injection via config).

### 15.3.4 Choosing between Redis and PostgreSQL

Rule of thumb:
- Redis is great for fast, ephemeral, cache-like skill stores (and simple ops).
- PostgreSQL is better for long-term governance: schema constraints, migrations, indexing, audit columns, and backup policies.

Most teams start with:
- dev: filesystem
- prod: pgsql (authoritative) + optional redis (cache/fast read path) as a later stage

## 15.4 Troubleshooting checklist (storage-focused)

1) Validate configuration (no I/O):
   - `python3 -m skills_runtime.cli.main skills preflight ...`
2) Scan and inspect errors/warnings:
   - `python3 -m skills_runtime.cli.main skills scan ...`
3) If `redis/pgsql` sources fail:
   - confirm `dsn_env` is present in environment
   - confirm dependencies (`redis`, `psycopg`) are installed
   - confirm credentials / network / firewall rules
4) If injection fails:
   - check `skills.injection.max_bytes`
   - check that the body loader can retrieve the body (filesystem path exists, Redis body key exists, PG row exists)

---

Prev: [`14-safety-deep-dive.md`](./14-safety-deep-dive.md)
