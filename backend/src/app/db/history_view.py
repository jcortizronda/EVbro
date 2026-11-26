"""Limits how far back a request reads the history tables.

`entitlements.limit_history` leaves a moment in `session.info` when the account only sees its recent history. From then on every
ORM SELECT of that session gets one more condition on the tables below, whatever route or service wrote the query: lists, totals,
a single row by id, the timeline. Nothing is deleted and nothing else is touched: sessions without that key (the poller, the
detectors, requests that change data, the account's export) read everything.
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.db.models import ChargingSession, CommandAudit, Drive, GeofenceEvent

FLOOR_KEY = "history_floor"          # the same key as `app.entitlements.HISTORY_FLOOR`
# What counts as recorded history, and the column that dates each row
DATED = ((Drive, Drive.start_time), (ChargingSession, ChargingSession.start_time), (GeofenceEvent, GeofenceEvent.timestamp),
         (CommandAudit, CommandAudit.timestamp))


@event.listens_for(Session, "do_orm_execute")
def _limit_history(state) -> None:
    floor = state.session.info.get(FLOOR_KEY)
    if floor is None or not state.is_select:
        return
    state.statement = state.statement.options(
        *(with_loader_criteria(model, column >= floor, include_aliases=True) for model, column in DATED))
