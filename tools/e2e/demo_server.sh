#!/usr/bin/env bash
# ISOLATED demo server for the browser tests:
#   - temporary SQLite and demo data (python -m app.seed_demo); never touches your PostgreSQL.
#   - no internet access (dead proxy): the poller does not call Chery.
#   - user: juan / una-contraseña-larga-1   ·   URL: http://127.0.0.1:${PORT:-8099}
# Usage:  tools/e2e/demo_server.sh start | stop
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT="${PORT:-8099}"
WORK="${E2E_DIR:-/tmp/evbro-e2e}"
PIDFILE="$WORK/server.pid"
USER_NAME="juan"; USER_PASS="una-contraseña-larga-1"; CODE="e2e-codigo"
ADMIN_NAME="admin"; ADMIN_PASS="contraseña-del-admin-1"

stop() { [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; }

case "${1:-start}" in
  stop) stop; exit 0 ;;
  start)
    stop; rm -rf "$WORK"; mkdir -p "$WORK/data"
    cd "$ROOT/backend"
    # shellcheck disable=SC1091
    . .venv/bin/activate
    export PYTHONPATH=src DATABASE_URL="sqlite+aiosqlite:///$WORK/e2e.db" DATA_DIR="$WORK/data" SETUP_CODE="$CODE"
    export ADMIN_PORT="${ADMIN_PORT:-8098}"             # the demo's admin console (tools/e2e/admin.py)
    export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9
    python - <<'PY'
import asyncio, os
assert os.environ["DATABASE_URL"].startswith("sqlite"), "solo SQLite"
from app.db.session import init_db, engine
asyncio.run(init_db()); asyncio.run(engine.dispose())
PY
    python -m app.seed_demo >/dev/null
    # The demo manager (owns the demo car, id 1). The administrator is created through the admin console below, like in real life.
    DEMO_USER="$USER_NAME" DEMO_PASS="$USER_PASS" python - <<'PY'
import asyncio, os
from app.db.models import AppUser
from app.db.session import async_session_factory, engine
from app.security import passwords
async def main():
    async with async_session_factory() as s:
        s.add(AppUser(id=1, username=os.environ["DEMO_USER"], role="manager", password_hash=passwords.hash_password(os.environ["DEMO_PASS"]),
                      vehicle_limit=-1, member_limit=-1))
        await s.commit()
    await engine.dispose()
asyncio.run(main())
PY
    nohup python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" > "$WORK/server.log" 2>&1 &
    echo $! > "$PIDFILE"
    for _ in $(seq 1 40); do curl --noproxy "*" -s -o /dev/null "http://127.0.0.1:$PORT/health" && break; sleep 0.5; done
    for _ in $(seq 1 40); do curl --noproxy "*" -s -o /dev/null "http://127.0.0.1:$ADMIN_PORT/api/auth/status" && break; sleep 0.5; done
    curl --noproxy "*" -s -o /dev/null -X POST "http://127.0.0.1:$ADMIN_PORT/api/auth/setup" -H "Origin: http://127.0.0.1:$ADMIN_PORT" \
      -H "Content-Type: application/json" \
      -d "{\"username\":\"$ADMIN_NAME\",\"password\":\"$ADMIN_PASS\",\"setup_code\":\"$CODE\"}"
    echo "Demo ready at http://127.0.0.1:$PORT  ($USER_NAME / $USER_PASS) · console http://127.0.0.1:$ADMIN_PORT ($ADMIN_NAME / $ADMIN_PASS) · log: $WORK/server.log"
    ;;
  *) echo "usage: $0 start|stop"; exit 2 ;;
esac
