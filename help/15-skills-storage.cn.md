<div align="center">

[中文](15-skills-storage.cn.md) | [English](15-skills-storage.md) | [Help](README.cn.md)

</div>

# 15. Skills 存储与来源：filesystem + Redis + PostgreSQL

本章回答两类“上线必问”的问题：

1) **Skills 在 workspace 里到底落在哪里？**（Studio / 本地开发）
2) **生产环境怎么把 Skills 当成一个可治理的存储？**（Redis/PGSQL sources、约束、故障形态）

内容以“能直接照着做”为目标，并对齐当前实现行为。

相关章节：
- Skills 基础（mention/scan/injection）：`help/05-skills-guide.cn.md`
- 配置字段（spaces/sources/scan/injection）：`help/02-config-reference.cn.md`
- 内部机制（scan vs inject、sources map）：`help/08-architecture-internals.cn.md`

## 15.1 心智模型：space/source + scan + inject

Skills 存储由两份列表配置驱动：

- `skills.spaces[]`：启用哪些 **namespace**，以及每个 namespace 从哪些 sources 读取
- `skills.sources[]`：具体的 **存储后端** 与其连接/参数

```text
mention token: $[namespace].skill_name
            │
            v
resolve(namespace, skill_name)
  │
  ├─ validate：namespace 已配置且启用
  ├─ scan()：从 sources 建索引（metadata-only）
  └─ inject()：按需读取正文（body_loader；受预算控制）
```

关键性质：
- **scan 只读元信息**（快、便宜）
- **inject 才读正文**（受 `skills.injection.max_bytes` 约束）

### 15.1.1 `resolve_mentions` 端到端链路图（发现/注入/工具执行边界）

下面的图把“配置驱动的发现（spaces/sources）”与“正文注入（inject）”以及“Phase 3 资产（actions/references）”的边界画清楚：

```text
user_task_text
  │
  ├─ SkillsManager.resolve_mentions(text)
  │    ├─ extract_skill_mentions()                 # 解析 $[namespace].skill_name token
  │    ├─ scan()                                   # 按 skills.scan.refresh_policy 决定是否刷新
  │    │    ├─ filesystem / redis / pgsql / in-memory  # metadata-only（不读正文/不读 bundle）
  │    │    └─ build index: (namespace, skill_name) → Skill
  │    └─ map mentions → Skill objects
  │
  ├─ Prompt injection（Agent loop）
  │    └─ SkillsManager.render_injected_skill(skill)
  │         └─ skill.body_loader()                 # 懒加载正文（受 skills.injection.max_bytes 约束）
  │
  └─ Phase 3 tools（仅在被调用时发生）
       ├─ skill_exec / skill_ref_read
       │    └─ （Redis）lazy fetch + safe extract bundle  # 仅工具路径需要；scan/inject 不读取 bundle
       └─ shell_exec / 受限文件读取 ...
            └─ approvals gate + OS sandbox fence
```

## 15.2 A）Filesystem 存储（workspace 友好）

### 15.2.1 Studio 默认落盘位置

Studio API 创建 skill 时，常见写入位置是：

```text
<workspace_root>/.skills_runtime_sdk/skills/
```

示例（仅一种可能的目录形态）：

```text
<workspace_root>/.skills_runtime_sdk/skills/
  web/
    article-writer/
      SKILL.md
  code/
    reviewer/
      SKILL.md
```

说明：
- 扫描器会在你配置的 `root` 下递归查找名字**恰好**是 `SKILL.md` 的文件。
- 目录层级 **不强制固定**；只要能扫描到 `SKILL.md` 即可。

### 15.2.2 Filesystem source 最小配置

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
        # 相对路径会以 workspace_root 为锚点 resolve。
        root: .skills_runtime_sdk/skills
```

### 15.2.3 多个 filesystem roots（治理分层）

常见做法是把不同用途的 skills 分层到不同 root：

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

价值：
- “system skills” 可以单独审阅/加锁/只读挂载
- “business skills” 可以更频繁更新
- sources 本身也可以用文件系统权限做隔离

### 15.2.4 Filesystem 运维最佳实践

- 把 `.skills_runtime_sdk/` 当作 **runtime 产物目录**（通常可安全删除并重建）。
- 若追求可复现交付，建议把“定版/可审阅”的 skills 存在仓库目录（例如 `skills/`），并用 filesystem source 指向它。
- skill 正文保持聚焦与可组合，关注 `skills.injection.max_bytes` 避免 prompt 膨胀。

## 15.3 B）生产级 sources：Redis 与 PostgreSQL

SDK 支持从远端 sources 扫描：

- `redis`：元信息存在 hash；正文存在单独的 key
- `pgsql`：元信息与正文存在一张表中

### 15.3.1 共同约束与治理

#### 必填 options（preflight 会校验）

不同 source 类型要求的 `source.options` 如下：

```text
filesystem: root
in-memory:  namespace
redis:      dsn_env + key_prefix
pgsql:      dsn_env + schema + table
```

#### DSN 通过环境变量注入（YAML 不放密钥）

远端 sources 使用 `dsn_env`，并从进程环境变量读取 DSN：

```yaml
skills:
  sources:
    - id: redis_main
      type: redis
      options:
        dsn_env: SKILLS_REDIS_DSN
        key_prefix: "skills:"
```

这样可以避免把凭据写进 overlay YAML / 仓库。

#### 依赖是可选的（失败时给结构化错误）

- Redis source 依赖 Python 包 `redis`
- PGSQL source 依赖 `psycopg`

缺依赖/连不上/查询失败都会变成可观测的 source-unavailable 错误（而不是静默失败）。

### 15.3.2 Redis source：数据契约（key/field 结构）

配置：

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

扫描模式（metadata-only）：

```text
{key_prefix}meta:{namespace}:*
```

每个命中的 key 是一个 Redis hash，其 fields 遵循最小契约：

```text
skill_name        string   （必填；slug）
description       string   （必填）
created_at        string   （必填；建议 RFC3339）
required_env_vars string   （可选；JSON 字符串：["ENV_A","ENV_B"]）
metadata          string   （可选；JSON 字符串：{"k":"v"}）
body_key          string   （可选）
body_size         int      （可选）
etag             string   （可选）
updated_at        string   （可选）
scope            string   （可选）
```

#### Redis bundles（Phase 3：actions / references 的最小资产协议）

当你希望 Redis skills 支持 Phase 3 的 `skill_exec` / `skill_ref_read` 时，需要额外提供一个 **zip bundle**（最小版只允许 `actions/` + `references/`）。

约束（重要）：
- `scan()` 仍然是 metadata-only：**不得**因为有 bundle 而在 scan 期读取 bundle key。
- bundle **仅在工具调用路径**（`skill_exec` / `skill_ref_read`）中按需读取；读取失败/校验失败必须 **fail-closed**（不执行命令、不读引用）。

新增/扩展的 metadata fields（位于 meta hash 内）：

```text
bundle_sha256   string   （建议必填；用于缓存/审计/避免 TOCTOU）
bundle_key      string   （可选；缺失时使用默认 key）
bundle_size     int      （可选；用于预算与可观测性）
bundle_format   string   （固定为 "zip"；不允许其它值）
```

bundle bytes 的默认 key（当 `bundle_key` 缺失时）：

```text
{key_prefix}bundle:{namespace}:{skill_name}
```

如果缺少 `body_key`，默认值为：

```text
{key_prefix}body:{namespace}:{skill_name}
```

注入（lazy body load）会通过 `GET <body_key>` 读取正文，期望值为 UTF-8 bytes 或 string。

推荐实践：
- `required_env_vars` 与 `metadata` 用 **JSON 字符串**（不要写成其它结构）
- `key_prefix` 作为边界，便于权限控制与批量清理

### 15.3.3 PostgreSQL source：表结构契约

配置：

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

扫描查询（metadata-only）要求表至少提供这些字段：

```text
SELECT id, namespace, skill_name, description, body_size, body_etag, created_at, updated_at,
       required_env_vars, metadata, scope
FROM "<schema>"."<table>"
WHERE enabled = TRUE AND namespace = %s
```

正文读取（lazy injection）会执行：

```text
SELECT body FROM "<schema>"."<table>" WHERE id = %s AND namespace = %s
```

#### 推荐最小 DDL

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

说明：
- `required_env_vars` 建议使用 `text[]`（运行时期望是 `list[str]`）。
- `metadata` 建议使用 `jsonb`（运行时期望是 dict-like）。
- `schema/table` 标识符会被运行时按安全 regex 校验（避免配置层注入）。

### 15.3.4 Redis vs PostgreSQL 怎么选

经验法则：
- Redis 更适合“快、轻、偏缓存”的技能存储。
- PostgreSQL 更适合“长期治理”：schema 约束、迁移、索引、审计字段、备份策略。

多数团队从以下组合起步：
- dev：filesystem
- prod：pgsql（权威源）→（可选）后续再引入 redis 做加速/缓存

## 15.4 排障清单（存储相关）

1) 配置静态校验（无 I/O）：
   - `python3 -m skills_runtime.cli.main skills preflight ...`
2) 扫描并查看 errors/warnings：
   - `python3 -m skills_runtime.cli.main skills scan ...`
3) `redis/pgsql` source 失败时：
   - 确认 `dsn_env` 在环境变量中存在
   - 确认依赖（`redis` / `psycopg`）已安装
   - 检查凭据/网络/防火墙
4) 注入失败时：
   - 检查 `skills.injection.max_bytes`
   - 确认正文可取（文件存在；Redis body key 存在；PG row/namespace 匹配）

---

上一章：[`14-safety-deep-dive.cn.md`](./14-safety-deep-dive.cn.md)
