#!/bin/sh
# Starts as root only to make sure the data volume belongs to the app user, then drops privileges for good.
# A volume created by an older image (or by Docker itself) is owned by root; without this step the app could not write its tokens,
# the encryption key or the uploaded files. Everything the app does afterwards runs as `app` (no root inside the container).
set -eu
DATA_DIR="${DATA_DIR:-/app/data}"
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R app:app "$DATA_DIR"
    exec gosu app "$@"
fi
exec "$@"
