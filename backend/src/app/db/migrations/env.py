"""Alembic environment: migrates the database the app settings point to, with the app's own async driver.

The URL comes, in this order, from `-x url=...` / `sqlalchemy.url` in the config (tests and one-off runs) or from `DATABASE_URL`
(the app). `render_as_batch` lets the same revisions run on SQLite (tests, demo) and PostgreSQL (production).
"""
from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base
from app.db.session import database_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return context.get_x_argument(as_dictionary=True).get("url") or config.get_main_option("sqlalchemy.url") or database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # Alembic is always invoked from a worker thread without an event loop (db/migrate.py) or from the CLI: a loop of its own is safe.
    asyncio.run(run_migrations_online())
