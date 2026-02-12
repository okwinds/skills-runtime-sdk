#!/usr/bin/env bash
set -euo pipefail

# Skills sources（redis/pgsql）集成验证入口（Docker，保留容器/数据用于人工检查）。
#
# 说明：
# - 与 `skills_sources_docker.sh` 的区别：本脚本**不会**在退出时 `docker compose down -v`。
# - 适用场景：你想在 Docker Desktop / CLI 里看到容器仍在运行，以及 Postgres/Redis 里实际写入的数据结构。
# - 清理请手动执行（见脚本末尾提示）。

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/scripts/integration/skills_sources_docker.compose.yml"

cd "$REPO_ROOT"

echo "[integration] compose file: $COMPOSE_FILE"
echo "[integration] starting services (redis/postgres)..."
docker compose -f "$COMPOSE_FILE" up -d redis postgres

echo "[integration] current services:"
docker compose -f "$COMPOSE_FILE" ps

echo ""
echo "[integration] running integration tests in runner..."
docker compose -f "$COMPOSE_FILE" run --rm runner

echo ""
echo "[integration] keeping redis/postgres running for inspection."
docker compose -f "$COMPOSE_FILE" ps

echo ""
echo "[integration] postgres: list tables (agent.*)"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U skills -d skills -c "\\dt agent.*" || true

echo ""
echo "[integration] postgres: describe table (agent.skills_catalog)"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U skills -d skills -c "\\d+ agent.skills_catalog" || true

echo ""
echo "[integration] postgres: sample rows (account/domain/skill_name/enabled/created_at/updated_at)"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U skills -d skills -c 'SELECT account, domain, skill_name, enabled, created_at, updated_at FROM agent.skills_catalog ORDER BY skill_name;' || true

echo ""
echo "[integration] redis: keys (pattern skills:*)"
docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli --scan --pattern 'skills:*' | head -n 80 || true

echo ""
echo "[integration] cleanup when you are done:"
echo "  docker compose -f \"$COMPOSE_FILE\" down -v --remove-orphans"
