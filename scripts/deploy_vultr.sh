#!/usr/bin/env bash
# One-command deploy to a fresh Ubuntu server (Vultr): ./scripts/deploy_vultr.sh root@SERVER_IP
# Needs: SSH access with your key, backend/.env filled in (DATABASE_URL for Tiger Cloud, keys,
# API_TOKEN, DOMAIN). Copies code + served models + training data, installs Docker, starts it.
set -euo pipefail
SERVER="${1:?usage: deploy_vultr.sh user@server}"
DEST=/opt/forecaster
cd "$(dirname "$0")/.."
[ -f backend/.env ] || { echo "backend/.env missing"; exit 1; }
grep -q "^DATABASE_URL=postgres" backend/.env || { echo "DATABASE_URL (Tiger Cloud) must be set — otherwise the server would start on an empty local database"; exit 1; }
grep -q '^API_TOKEN=.\+' backend/.env || echo "WARNING: API_TOKEN not set — write endpoints will be open to anyone"

ssh "$SERVER" "command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh; mkdir -p $DEST"
# code (the image builds the frontend and installs Python deps on the server)
tar --exclude='./frontend/node_modules' --exclude='./backend/.venv' --exclude='./data' --exclude='./artifacts' \
    --exclude='./.git' --exclude='**/__pycache__' -czf - . | ssh "$SERVER" "tar -xzf - -C $DEST"
# the serving model only (newest production version, or MODEL=demand_vN), plus plans and reports.
# Retrains on the server add their own versions next to it.
MODEL="${MODEL:-$(ls -d artifacts/models/demand_v* | sed 's#.*/##' | sort -V | tail -1)}"
echo "uploading model $MODEL"
tar -czf - "artifacts/models/$MODEL" artifacts/*.json artifacts/*.parquet artifacts/store_recs 2>/dev/null \
    | ssh "$SERVER" "tar -xzf - -C $DEST"
tar -czf - data/processed data/cache | ssh "$SERVER" "tar -xzf - -C $DEST"
ssh "$SERVER" "cd $DEST && docker compose -f docker-compose.prod.yml up -d --build && docker compose -f docker-compose.prod.yml ps"
echo "Deployed. If DOMAIN is set, point its A record at the server IP; HTTPS is automatic."
