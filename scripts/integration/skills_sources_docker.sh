#!/usr/bin/env bash
set -euo pipefail

# Skills sources（redis/pgsql）集成验证入口（Docker）。
#
# 说明：
# - 仅用于“真实 I/O 验证”；默认离线回归仍以 `./scripts/pytest.sh` 为准。
# - 本脚本会启动 redis/postgres，并在 runner 容器内安装依赖后运行 integration tests。

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/scripts/integration/skills_sources_docker.compose.yml"

cd "$REPO_ROOT"

cleanup() {
  docker compose -f "$COMPOSE_FILE" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" up -d redis postgres

docker compose -f "$COMPOSE_FILE" ps

if ! docker compose -f "$COMPOSE_FILE" run --rm runner; then
  echo ""
  echo "[integration] runner 失败，输出 redis/postgres 日志用于排查..."
  docker compose -f "$COMPOSE_FILE" logs --no-color redis postgres || true
  exit 1
fi
