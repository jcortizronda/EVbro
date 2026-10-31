#!/usr/bin/env bash
# Daily backup of EVBro: a dump of the database, an archive of the data volume (tokens, encrypted documents/invoices/photos,
# VAPID key, secret.key) and deploy/.env, which holds ENCRYPTION_KEY (without the key nothing of the above can be read).
# Keeps KEEP_DAYS days in backups/ and removes Docker build leftovers older than 3 days (each deploy leaves hundreds of MB).
#   Install (as root, on the server):   0 4 * * * /opt/evbro/deploy/backup.sh >> /opt/evbro/backups/backup.log 2>&1
# Copy backups/ OFF the server (NAS, Proxmox Backup Server...): this protects against a bad migration, not against losing the machine.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${BACKUP_DIR:-$ROOT/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
DB_CONTAINER="${DB_CONTAINER:-evbro-db}"
DATA_VOLUME="${DATA_VOLUME:-evbro_evbro_data}"
if [ -f "$ROOT/deploy/.env" ]; then
    set -a; . "$ROOT/deploy/.env"; set +a           # POSTGRES_USER / POSTGRES_DB, if the install changed them
fi
DB_USER="${POSTGRES_USER:-evbro}"
DB_NAME="${POSTGRES_DB:-evbro}"
STAMP="$(date +%F)"

umask 077
mkdir -p "$DEST"
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$DEST/evbro-db-$STAMP.sql.gz"
[ -f "$ROOT/deploy/.env" ] && cp "$ROOT/deploy/.env" "$DEST/env-$STAMP"
docker run --rm -v "$DATA_VOLUME":/d:ro -v "$DEST":/b alpine:3.20 tar czf "/b/evbro-data-$STAMP.tgz" -C /d .
chmod 600 "$DEST/evbro-data-$STAMP.tgz"           # created by the container as root: not covered by our umask
find "$DEST" -maxdepth 1 \( -name 'evbro-db-*.sql.gz' -o -name 'evbro-data-*.tgz' -o -name 'env-*' \) -mtime +"$KEEP_DAYS" -delete
docker builder prune -af --filter "until=72h" >/dev/null 2>&1 || true
docker image prune -f >/dev/null 2>&1 || true
echo "$(date -Is) ok: $DEST/evbro-db-$STAMP.sql.gz ($(du -h "$DEST/evbro-db-$STAMP.sql.gz" | cut -f1)) + evbro-data-$STAMP.tgz ($(du -h "$DEST/evbro-data-$STAMP.tgz" | cut -f1))"
