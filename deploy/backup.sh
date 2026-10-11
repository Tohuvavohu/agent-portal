#!/usr/bin/env bash
# Back up the database and the session archive into deploy/backups/<timestamp>/.
# Safe to run while the portal is up: pg_dump takes a consistent snapshot.
# deploy/.env is NOT copied here — store it separately (it holds the secrets
# that every cookie, token and the database password depend on).
set -euo pipefail

cd "$(dirname "$0")"
dest="backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$dest"

docker compose exec -T db pg_dump -U agent_portal -d agent_portal --format=custom \
  > "$dest/db.dump"
docker compose exec -T portal tar -C /data -czf - archive > "$dest/archive.tar.gz"

echo "Backup written to deploy/$dest"
