"""Alembic owns the schema: the baseline matches the models, the app stamps/upgrades at startup, and the legacy path still works."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import migrate
from app.db.models import Base


@pytest.fixture
def scratch_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/migrations.db"


async def _tables(url: str) -> dict:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            def _read(sync_conn):
                insp = inspect(sync_conn)
                return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}
            return await conn.run_sync(_read)
    finally:
        await engine.dispose()


async def _version(url: str) -> str | None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    finally:
        await engine.dispose()


async def test_upgrade_head_on_an_empty_database_builds_every_model_table(scratch_url) -> None:
    await migrate.upgrade_to_head(scratch_url)
    tables = await _tables(scratch_url)
    for table in Base.metadata.sorted_tables:
        assert table.name in tables, table.name
        assert {c.name for c in table.columns} <= tables[table.name], table.name
    assert await _version(scratch_url) == migrate.head_revision()


async def test_the_app_stamps_an_untracked_database_and_then_only_upgrades(db) -> None:
    """`init_db` on the test engine: first start takes the legacy path and stamps; the second start finds it tracked."""
    from app.db.session import engine, init_db
    async with engine.begin() as conn:                       # the `db` fixture already created the tables: an untracked, pre-Alembic database
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    assert not await migrate.is_tracked(engine)
    await init_db()
    assert await migrate.is_tracked(engine)
    assert await _version(str(engine.url)) == migrate.head_revision()
    await init_db()                                          # tracked: runs `upgrade head` (nothing pending) without touching the data
    assert await _version(str(engine.url)) == migrate.head_revision()


def test_every_revision_file_is_reachable_from_head() -> None:
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(migrate.alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1, f"more than one head: {heads} (two revisions branch from the same parent; merge them)"


def test_revision_ids_fit_alembic_version_column() -> None:
    """`alembic_version.version_num` is VARCHAR(32) in PostgreSQL; a longer id made the whole upgrade roll back in production
    (2026-10-30) while SQLite, which does not enforce lengths, let the tests pass."""
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(migrate.alembic_config())
    too_long = [r.revision for r in script.walk_revisions() if len(r.revision) > 32]
    assert not too_long, f"Identificadores de revisión de más de 32 caracteres: {too_long}"


async def test_0016_truncates_the_gps_decimals_of_odometer_drives_only(scratch_url) -> None:
    """Drives detected from the car (with odometer) lose the decimals older versions borrowed from the GPS track; drives built
    from GPS alone (no odometer) keep theirs."""
    import asyncio

    from alembic import command
    await asyncio.to_thread(command.upgrade, migrate.alembic_config(scratch_url), "0015_journeys")
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO vehicles (vin, name) VALUES ('LSJA0000000000016', 'Coche')"))
            await conn.execute(text(
                "INSERT INTO drives (vin, start_time, distance_km, start_odometer, end_odometer) VALUES "
                "('LSJA0000000000016', '2026-08-01 06:00:00', 12.7, 1000, 1012), "
                "('LSJA0000000000016', '2026-08-02 06:00:00', 6.0, 1012, 1018), "
                "('LSJA0000000000016', '2026-08-03 06:00:00', 4.35, NULL, NULL)"))
        await migrate.upgrade_to_head(scratch_url)
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT distance_km FROM drives ORDER BY start_time"))).scalars().all()
    finally:
        await engine.dispose()
    assert rows == [12.0, 6.0, 4.35]
    assert await _version(scratch_url) == migrate.head_revision()
