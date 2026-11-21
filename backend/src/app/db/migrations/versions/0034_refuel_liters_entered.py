"""Refuels: litres typed by the user (`refuels.liters_entered`). Idempotent.

A detected refuel carries the litres the gauge suggests (% risen x tank) and is tagged «estimados». When the user corrected them
with the ticket the tag stayed, because nothing remembered that the figure was no longer the estimate. Existing refuels whose litres
no whole-litre tank explains are marked as typed (the tank of the sheet may have changed since they were detected).

Revision ID: 0034_refuel_liters_entered
Revises: 0033_drive_unseen
Create Date: 2026-11-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0034_refuel_liters_entered"
down_revision = "0033_drive_unseen"
branch_labels = None
depends_on = None


def _typed(liters: float, risen_pct: float, tank: float) -> bool:
    """Whether some litres were typed rather than estimated. The estimate is `round(risen % x tank, 1)` with the tank of that
    moment, and the tank of the sheet may have changed since; so the litres only count as typed when no whole-litre tank gives
    them (an estimate left as typed would lose its tag wrongly; a typed figure left as estimate only keeps a tag it had)."""
    if abs(liters - round(liters, 1)) > 1e-6:
        return True                               # estimates had one decimal until this revision: two (23.54, a ticket) were typed
    if abs(liters - round(risen_pct / 100.0 * tank, 1)) < 0.05 or risen_pct <= 0:
        return False
    implied = liters * 100.0 / risen_pct
    return abs(implied - round(implied)) > 0.05 * 100.0 / risen_pct + 0.01


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("refuels")}
    if "liters_entered" not in cols:              # a database built from the models already has it: the marking below still runs
        op.add_column("refuels", sa.Column("liters_entered", sa.Boolean(), nullable=False, server_default=sa.false()))
    rows = bind.execute(sa.text(
        "SELECT r.id, r.liters, r.fuel_before_pct, r.fuel_after_pct, v.fuel_tank_liters FROM refuels r "
        "LEFT JOIN vehicles v ON v.vin = r.vin "
        "WHERE r.detected = :yes AND r.fuel_before_pct IS NOT NULL AND r.fuel_after_pct IS NOT NULL AND r.liters IS NOT NULL"),
        {"yes": True}).fetchall()
    typed = []
    for rid, liters, before, after, tank in rows:
        if _typed(liters, after - before, tank or 50.0):
            typed.append(rid)
    for rid in typed:
        bind.execute(sa.text("UPDATE refuels SET liters_entered = :yes WHERE id = :id"), {"yes": True, "id": rid})


def downgrade() -> None:
    op.drop_column("refuels", "liters_entered")
