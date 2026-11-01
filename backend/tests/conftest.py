"""EVBro test harness.

The snapshots in `tests/core/` come from the Home Assistant integration, which keeps them in
`snapshots/` (and not in syrupy's default `__snapshots__/`). That folder is kept so the core tests
can be compared and synced between both repositories without renaming anything.
"""
from __future__ import annotations

import os
import tempfile

# EVBro's configuration is read when `app.config` is imported: the test environment must be
# set BEFORE any module imports it (this conftest is loaded first).
_TMP = tempfile.mkdtemp(prefix="evbro-tests-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP}/test.db"
os.environ["DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["ADMIN_PORT"] = "0"          # the admin console is tested through its ASGI app; no real second port in tests
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


# ───────────────────────── EVBro fixtures (app, DB, encryption) ─────────────────────────
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


@pytest_asyncio.fixture
async def db():
    """Empty SQLite database (freshly created tables) on the app's real engine."""
    from app.db.models import Base
    from app.db.session import engine

    ensure_test_database(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
