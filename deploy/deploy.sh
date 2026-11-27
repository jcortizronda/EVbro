#!/usr/bin/env bash
# Deploys the latest commit of the source tree on this server, the same way every time, and keeps a way back.
#   1. a dump of the database, checked, in backups/pre-deploy-<date>.sql.gz
#   2. git fetch + fast-forward to origin/main (refuses anything else)
#   3. builds the image and tags it evbro-local:<commit>, so the nightly `docker image prune` keeps it (rollback.sh needs it)
#   4. restarts the app and waits for /health/ready; on failure says how to go back
#   5. keeps the last KEEP_IMAGES tagged builds and writes one line to backups/deploy.log
# Usage (as root, on the server):   /opt/evbro/deploy/deploy.sh
# For an install that runs the public image instead: docker compose pull && docker compose up -d (docs/deployment.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${BACKUP_DIR:-$ROOT/backups}"
KEEP_IMAGES="${KEEP_IMAGES:-5}"
READY_URL="${READY_URL:-}"
cd "$ROOT/deploy"

env_value() {    # one value of deploy/.env, which is Compose syntax, not shell (see backup.sh)
    [ -f .env ] || return 0
    sed -n "s/^[[:space:]]*$1=//p" .env | tail -n 1 | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
DB_USER="$(env_value POSTGRES_USER)"; DB_USER="${DB_USER:-evbro}"
DB_NAME="$(env_value POSTGRES_DB)"; DB_NAME="${DB_NAME:-evbro}"
if [ -z "$READY_URL" ]; then
    BIND="$(env_value BIND_ADDRESS)"; PORT="$(env_value WEB_PORT)"
    [ -z "$BIND" ] || [ "$BIND" = "0.0.0.0" ] && BIND=127.0.0.1
    READY_URL="http://$BIND:${PORT:-8080}/health/ready"
fi
log() { echo "$(date -Is) $*" | tee -a "$DEST/deploy.log"; }

umask 077
mkdir -p "$DEST"
BEFORE="$(git -C "$ROOT" rev-parse --short HEAD)"
DUMP="$DEST/pre-deploy-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T db pg_dump --no-owner --no-privileges -U "$DB_USER" "$DB_NAME" | gzip > "$DUMP"
zcat "$DUMP" | tail -n 5 | grep -c "PostgreSQL database dump complete" > /dev/null || { log "ABORTED: the pre-deploy dump is incomplete ($DUMP)"; exit 1; }

git -C "$ROOT" fetch -q
git -C "$ROOT" merge -q --ff-only origin/main
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD)"
if [ "$COMMIT" = "$BEFORE" ] && [ "${FORCE:-0}" != "1" ]; then
    log "nothing to deploy: already at $COMMIT (FORCE=1 rebuilds anyway)"; rm -f "$DUMP"; exit 0
fi

IMAGE="$(docker compose config --images app | head -n 1)"
# The build that is running now gets a tag too, if it has none yet: it is the one rollback.sh would go back to
docker image inspect "evbro-local:$BEFORE" >/dev/null 2>&1 || docker tag "$IMAGE" "evbro-local:$BEFORE" 2>/dev/null || true
EVBRO_COMMIT="$(git -C "$ROOT" rev-parse HEAD)" docker compose build -q app
docker tag "$IMAGE" "evbro-local:$COMMIT"
docker compose up -d app

for _ in $(seq 1 60); do
    if curl -fs -o /dev/null "$READY_URL"; then
        log "deployed $BEFORE -> $COMMIT (dump: $(basename "$DUMP"))"
        # Oldest first by the date of their commit (an image's own creation date says nothing: the build can reuse its layers)
        for tag in $(docker images --format '{{.Tag}}' evbro-local); do
            echo "$(git -C "$ROOT" log -1 --format=%ct "$tag" 2>/dev/null || echo 0) $tag"
        done | sort -n | head -n -"$KEEP_IMAGES" | while read -r _ tag; do docker rmi "evbro-local:$tag" >/dev/null 2>&1 || true; done
        exit 0
    fi
    sleep 3
done
log "FAILED: $COMMIT is not ready after 3 min. Back to the previous version with: deploy/rollback.sh $BEFORE $DUMP"
exit 1
