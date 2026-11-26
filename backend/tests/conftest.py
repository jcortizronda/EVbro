"""EVbro test harness.

The snapshots in `tests/core/` come from the Home Assistant integration, which keeps them in
`snapshots/` (and not in syrupy's default `__snapshots__/`). That folder is kept so the core tests
can be compared and synced between both repositories without renaming anything.
"""
from __future__ import annotations

import os
import shutil
import tempfile

# EVbro's configuration is read when `app.config` is imported: the test environment must be
# set BEFORE any module imports it (this conftest is loaded first).
_TMP = tempfile.mkdtemp(prefix="evbro-tests-")
_DB_FILE = os.path.join(_TMP, "test.db")
_TEMPLATE_FILE = os.path.join(_TMP, "template.db")      # the empty schema, built once per run (see `db`)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_FILE}"
os.environ["DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["ADMIN_PORT"] = "0"          # the admin console is tested through its ASGI app; no real second port in tests
# no download of the Ministry's fuel station list from tests: the tests of services/fuel_stations.py turn it on with a fake source
os.environ["FUEL_PRICES_ENABLED"] = "false"
for _k in ("EBRO_PHONE", "EBRO_PASSWORD", "EBRO_PIN", "ENCRYPTION_KEY", "SETUP_CODE"):
    os.environ.pop(_k, None)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.amber import AmberSnapshotExtension


class _EbroSnapshotExtension(AmberSnapshotExtension):
    snapshot_dirname = "snapshots"


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(_EbroSnapshotExtension)


# ───────────────────────── EVbro fixtures (app, DB, encryption) ─────────────────────────
@pytest.fixture(autouse=True)
def _test_names_resolve_without_dns(monkeypatch):
    """Web Push endpoints and webhooks are checked against the server's own network, which resolves their host name:
    the names the tests use get a fixed public address, with no DNS query. A test may still replace `socket.getaddrinfo`."""
    import socket

    real = socket.getaddrinfo
    known = {"fcm.googleapis.com", "push.example"}

    def resolve(host, *args, **kwargs):
        if host in known:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Isolated data folder with its own encryption key."""
    from app.config import settings
    from app.security import crypto

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", None)
    crypto.reset_key_cache()
    yield tmp_path
    crypto.reset_key_cache()


def ensure_test_database(engine) -> None:
    """The fixtures run `drop_all`: they must never point at a real database."""
    assert engine.url.get_backend_name() == "sqlite", (
        f"Tests may only drop tables in SQLite, not in {engine.url.render_as_string(hide_password=True)}")


def _remove_database_files() -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.remove(_DB_FILE + suffix)
        except FileNotFoundError:
            pass


@pytest_asyncio.fixture
async def db():
    """Empty SQLite database (freshly created tables) on the app's real engine.

    The schema is created once per run into a template file and copied for each test. Dropping and creating every table took
    about 1.3 s per test, most of the 14 minutes of the whole suite (measured on 2026-11-09); copying the file takes milliseconds
    and leaves exactly the same empty schema."""
    from app.db.models import Base
    from app.db.session import engine

    ensure_test_database(engine)
    await engine.dispose()                                     # no pooled connection may keep the old file open
    if not os.path.exists(_TEMPLATE_FILE):
        _remove_database_files()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()
        shutil.copyfile(_DB_FILE, _TEMPLATE_FILE)
    _remove_database_files()
    shutil.copyfile(_TEMPLATE_FILE, _DB_FILE)
    yield engine
    await engine.dispose()
