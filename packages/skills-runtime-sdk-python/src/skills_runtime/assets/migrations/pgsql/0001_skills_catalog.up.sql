BEGIN;

-- Skills Runtime SDK Skills Catalog (Phase 2)
-- 说明：
-- - 该迁移用于 Skills PgSQL source 的最小生产化表结构（metadata-only scan + lazy-load inject）。
-- - 需与 `docs/specs/skills-runtime-sdk/docs/skills-sources-contract.md` 的 PgSQL 契约保持一致。

CREATE SCHEMA IF NOT EXISTS "agent";

CREATE TABLE IF NOT EXISTS "agent"."skills_catalog" (
  id BIGSERIAL PRIMARY KEY,

  account TEXT NOT NULL,
  domain TEXT NOT NULL,
  skill_name TEXT NOT NULL,
  description TEXT NOT NULL,

  body TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,

  body_size INTEGER NULL,
  body_etag TEXT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NULL,

  required_env_vars JSONB NULL,
  metadata JSONB NULL,
  scope TEXT NULL
);

-- 全局唯一：同一 (account, domain) 内 skill_name 唯一
CREATE UNIQUE INDEX IF NOT EXISTS "skills_catalog_account_domain_name_uk"
  ON "agent"."skills_catalog" (account, domain, skill_name);

-- 供 scan 查询过滤 enabled/account/domain
CREATE INDEX IF NOT EXISTS "skills_catalog_account_domain_enabled_idx"
  ON "agent"."skills_catalog" (account, domain, enabled);

COMMIT;
