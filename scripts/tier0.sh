#!/usr/bin/env bash
set -euo pipefail

# Tier-0 deterministic offline gate.
#
# 目标：
# - 作为 CI gate 的唯一默认入口：离线、确定性、可重复；
# - 覆盖 SDK + Studio（fake LLM）+ 前端单测三层。
#
# 用法：
#   bash scripts/tier0.sh

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

echo "[tier0] sdk+repo unit tests"
bash scripts/pytest.sh

echo "[tier0] studio backend offline e2e (fake LLM)"
bash packages/skills-runtime-studio-mvp/backend/scripts/pytest.sh

echo "[tier0] studio frontend unit tests"
npm -C packages/skills-runtime-studio-mvp/frontend ci
npm -C packages/skills-runtime-studio-mvp/frontend test

