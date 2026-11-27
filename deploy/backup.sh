#!/usr/bin/env bash
# Daily backup of EVbro: a dump of the database, an archive of the data volume (tokens, encrypted documents/invoices/photos,
# VAPID key, secret.key) and deploy/.env, which holds ENCRYPTION_KEY (without the key nothing of the above can be read).
# Keeps KEEP_DAYS days in backups/ (KEEP_PRE_DAYS for the hand-made pre-*.sql.gz dumps) and removes Docker build leftovers older than 3 days (each deploy leaves hundreds of MB).
#   Install (as root, on the server):   0 4 * * * /opt/evbro/deploy/backup.sh >> /opt/evbro/backups/backup.log 2>&1
# Copy backups/ OFF the server (NAS, Proxmox Backup Server...): this protects against a bad migration, not against losing the machine.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${BACKUP_DIR:-$ROOT/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
KEEP_PRE_DAYS="${KEEP_PRE_DAYS:-30}"
DB_CONTAINER="${DB_CONTAINER:-evbro-db}"
DATA_VOLUME="${DATA_VOLUME:-evbro_evbro_data}"
env_value() {
    # One value of deploy/.env. The file is NOT sourced: it is Compose syntax, not shell, and a line such as
    # `SMTP_FROM=EVbro <mail@example.com>` is a shell syntax error that used to stop the whole backup.
    [ -f "$ROOT/deploy/.env" ] || return 0
    sed -n "s/^[[:space:]]*$1=//p" "$ROOT/deploy/.env" | tail -n 1 | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
DB_USER="${POSTGRES_USER:-$(env_value POSTGRES_USER)}"      # the install may have changed them
DB_NAME="${POSTGRES_DB:-$(env_value POSTGRES_DB)}"
DB_USER="${DB_USER:-evbro}"
DB_NAME="${DB_NAME:-evbro}"
STAMP="$(date +%F)"

umask 077
mkdir -p "$DEST"
# Each file is written under a temporary name, checked, and only then given its real name: a dump cut short (the database
# stopped, the disk full) used to stay as the day's backup, looking fine (operations audit, 27-11-2026). Any failure ends the
# script with an error, which is what the off-site copy and its alert look at (deploy/offsite.sh).
fail() { echo "$(date -Is) ERROR: $*" >&2; rm -f "$DEST"/*.part; exit 1; }
docker exec "$DB_CONTAINER" pg_dump --no-owner --no-privileges -U "$DB_USER" "$DB_NAME" | gzip > "$DEST/evbro-db-$STAMP.sql.gz.part" || fail "pg_dump"
gzip -t "$DEST/evbro-db-$STAMP.sql.gz.part" || fail "the database dump is not a valid gzip"
zcat "$DEST/evbro-db-$STAMP.sql.gz.part" | tail -n 5 | grep -c "PostgreSQL database dump complete" > /dev/null || fail "the database dump is incomplete"
mv "$DEST/evbro-db-$STAMP.sql.gz.part" "$DEST/evbro-db-$STAMP.sql.gz"
[ -f "$ROOT/deploy/.env" ] && cp "$ROOT/deploy/.env" "$DEST/env-$STAMP"
docker run --rm -v "$DATA_VOLUME":/d:ro -v "$DEST":/b alpine:3.20 tar czf "/b/evbro-data-$STAMP.tgz.part" -C /d . || fail "archive of the data volume"
chmod 600 "$DEST/evbro-data-$STAMP.tgz.part"      # created by the container as root: not covered by our umask
tar tzf "$DEST/evbro-data-$STAMP.tgz.part" > /dev/null || fail "the archive of the data volume is not readable"
mv "$DEST/evbro-data-$STAMP.tgz.part" "$DEST/evbro-data-$STAMP.tgz"
find "$DEST" -maxdepth 1 \( -name 'evbro-db-*.sql.gz' -o -name 'evbro-data-*.tgz' -o -name 'env-*' \) -mtime +"$KEEP_DAYS" -delete
# The dumps taken by hand before a deploy or a data fix (pre-*.sql.gz): kept longer, but not for ever, and readable only by root
find "$DEST" -maxdepth 1 -name 'pre-*.sql.gz' -mtime +"$KEEP_PRE_DAYS" -delete
find "$DEST" -maxdepth 1 -type f -name '*.sql.gz' -perm /077 -exec chmod 600 {} +
docker builder prune -af --filter "until=72h" >/dev/null 2>&1 || true
docker image prune -f >/dev/null 2>&1 || true      # only untagged images: deploy.sh tags every build it deploys (evbro-local:<commit>)
echo "$(date -Is) ok: $DEST/evbro-db-$STAMP.sql.gz ($(du -h "$DEST/evbro-db-$STAMP.sql.gz" | cut -f1)) + evbro-data-$STAMP.tgz ($(du -h "$DEST/evbro-data-$STAMP.tgz" | cut -f1))"
