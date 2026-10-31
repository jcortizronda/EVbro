"""Schema migrations with Alembic, driven by the app at startup.

How a database gets here (see `session.init_db`):
  * **tracked** (it has `alembic_version`): only `upgrade head` runs; the pending revisions in `migrations/versions/` are applied.
  * **not tracked** (empty, or created before Alembic): the legacy path builds the baseline schema and the database is stamped at
    head, so the next start takes the first branch.

Alembic's commands are synchronous and `env.py` opens its own event loop, so they run in a worker thread.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
# Alembic announces every autogenerate plugin at INFO on each start: noise in the server log. "Running upgrade/stamp" lines stay.
logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)


def alembic_config(url: Optional[str] = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head() or ""


async def is_tracked(engine: AsyncEngine) -> bool:
    """Does this database already have Alembic's version table?"""
    async with engine.connect() as conn:
        return await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("alembic_version"))


async def upgrade_to_head(url: Optional[str] = None) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")


async def stamp_head(url: Optional[str] = None) -> None:
    await asyncio.to_thread(command.stamp, alembic_config(url), "head")
