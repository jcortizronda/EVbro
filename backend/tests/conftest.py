"""Arnés de test de EVBro.

Los snapshots de `tests/core/` vienen de la integración de Home Assistant, que los guarda en
`snapshots/` (y no en el `__snapshots__/` por defecto de syrupy). Se mantiene esa carpeta para poder
comparar y sincronizar los tests del core entre ambos repositorios sin renombrar nada.
"""
from __future__ import annotations

import os
import tempfile

# La configuración de EVBro se lee al importar `app.config`: hay que fijar el entorno de
# test ANTES de que cualquier módulo lo importe (este conftest se carga el primero).
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


# ───────────────────────── fixtures de EVBro (app, BD, cifrado) ─────────────────────────
@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Carpeta de datos aislada con una clave de cifrado propia."""
    from app.config import settings
    from app.security import crypto

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", None)
    crypto.reset_key_cache()
    yield tmp_path
    crypto.reset_key_cache()


def asegurar_bd_de_test(engine) -> None:
    """Las fixtures hacen `drop_all`: jamás deben apuntar a una BD real."""
    assert engine.url.get_backend_name() == "sqlite", (
        f"Los tests solo pueden borrar tablas en SQLite, no en {engine.url.render_as_string(hide_password=True)}")


@pytest_asyncio.fixture
async def db():
    """Base de datos SQLite vacía (tablas recién creadas) sobre el motor real de la app."""
    from app.db.models import Base
    from app.db.session import engine

    asegurar_bd_de_test(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
