#!/usr/bin/env bash
# Goes back to a build that deploy.sh tagged, and, unless --keep-db, to the database of a dump.
#   deploy/rollback.sh <commit> <dump.sql.gz>      the usual case: the new version migrated the database, the old code cannot read it
#   deploy/rollback.sh <commit> --keep-db          only the code (the deploy did not touch the database)
# Restoring the dump loses what was written after it (normally the minutes since the deploy). The data volume is not touched.
# The restore is the one tested by the release audit of 27-11-2026 (0 errors): empty database, then the dump with ON_ERROR_STOP.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMMIT="${1:?usage: rollback.sh <commit> <dump.sql.gz | --keep-db>}"
SOURCE="${2:?usage: rollback.sh <commit> <dump.sql.gz | --keep-db>}"
cd "$ROOT/deploy"
env_value() {
    [ -f .env ] || return 0
    sed -n "s/^[[:space:]]*$1=//p" .env | tail -n 1 | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
DB_USER="$(env_value POSTGRES_USER)"; DB_USER="${DB_USER:-evbro}"
DB_NAME="$(env_value POSTGRES_DB)"; DB_NAME="${DB_NAME:-evbro}"
DEST="${BACKUP_DIR:-$ROOT/backups}"

docker image inspect "evbro-local:$COMMIT" >/dev/null 2>&1 || { echo "No build tagged evbro-local:$COMMIT (docker images evbro-local)"; exit 1; }
if [ "$SOURCE" != "--keep-db" ]; then
    [ -f "$SOURCE" ] || { echo "No such dump: $SOURCE"; exit 1; }
    zcat "$SOURCE" | tail -n 5 | grep -c "PostgreSQL database dump complete" > /dev/null || { echo "The dump is incomplete: $SOURCE"; exit 1; }
fi

docker compose stop app
if [ "$SOURCE" != "--keep-db" ]; then
    # A safety copy of what is there now, in case the rollback itself was the mistake
    umask 077
    docker compose exec -T db pg_dump --no-owner --no-privileges -U "$DB_USER" "$DB_NAME" | gzip > "$DEST/pre-rollback-$(date +%Y%m%d-%H%M%S).sql.gz"
    docker compose exec -T db dropdb -U "$DB_USER" "$DB_NAME"
    docker compose exec -T db createdb -U "$DB_USER" "$DB_NAME"
    zcat "$SOURCE" | docker compose exec -T db psql -q -U "$DB_USER" -v ON_ERROR_STOP=1 "$DB_NAME" > /dev/null
fi
IMAGE="$(docker compose config --images app | head -n 1)"
docker tag "evbro-local:$COMMIT" "$IMAGE"
docker compose up -d --no-build app
echo "$(date -Is) rolled back to $COMMIT ($([ "$SOURCE" = "--keep-db" ] && echo "database kept" || echo "database from $(basename "$SOURCE")"))" | tee -a "$DEST/deploy.log"
echo "The source tree is still at $(git -C "$ROOT" rev-parse --short HEAD): the next deploy.sh would bring that version back."
