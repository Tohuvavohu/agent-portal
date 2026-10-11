#!/usr/bin/env bash
# Restore a backup made by backup.sh: ./restore.sh backups/<timestamp>
# Replaces the current database and archive. The portal is stopped while the
# data is swapped and started again afterwards; it runs any migrations the
# restored database is missing on boot.
set -euo pipefail

cd "$(dirname "$0")"
src=${1:?usage: ./restore.sh backups/<timestamp>}
[[ -f $src/db.dump && -f $src/archive.tar.gz ]] || {
  echo "$src does not look like a backup (needs db.dump and archive.tar.gz)" >&2
  exit 1
}

read -r -p "This replaces the current database and archive. Type 'restore' to continue: " confirm
[[ $confirm == restore ]] || { echo "Aborted."; exit 1; }

docker compose stop portal
docker compose up -d db
until docker compose exec -T db pg_isready -U agent_portal -d agent_portal >/dev/null; do sleep 1; done

docker compose exec -T db dropdb -U agent_portal --if-exists --force agent_portal
docker compose exec -T db createdb -U agent_portal agent_portal
docker compose exec -T db pg_restore -U agent_portal -d agent_portal --no-owner < "$src/db.dump"

docker compose run --rm --no-deps -T --entrypoint sh portal \
  -c 'find /data/archive -mindepth 1 -delete && tar -C /data -xzf -' < "$src/archive.tar.gz"

docker compose up -d
echo "Restored from deploy/$src"
