"""Tests for `core/session.py` — token health (session keep-alive).

The module only orchestrates: all its interesting logic sits in four seams towards `wake`.
Mocking them leaves an effectively pure module, and the branch that really matters is
**EXPIRED vs NET_ERROR**: it decides whether a user sees the
«Reautenticar» card. Mixing them up on a network outage would make them re-authenticate for nothing.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core import session


def _ctx(*, refresh_reason: str = "", refresh_ts: float | None = None):
    """Minimal context: `check()` only looks at `state.refresh_reason` and `state.refresh_ts`."""
    return SimpleNamespace(
        state=SimpleNamespace(
            refresh_reason=refresh_reason,
            refresh_ts=time.time() if refresh_ts is None else refresh_ts,
        )
    )


# ───────────────────────── check() ─────────────────────────


def test_check_ok() -> None:
    with patch.object(session.wake, "_bff_login", return_value=("token", "tuser")):
        ok, detalle, status = session.check(_ctx())

    assert (ok, status) == (True, session.STATUS_OK)
    assert "activa" in detalle


def test_check_exception_is_network_error_not_expired_session() -> None:
    """A login exception (DNS, TLS, timeout) is NOT a dead session."""
    with patch.object(session.wake, "_bff_login", side_effect=OSError("dns")):
        ok, detalle, status = session.check(_ctx())

    assert ok is False
    assert status == session.STATUS_NET_ERROR
    assert "OSError" in detalle


def test_check_failed_login_without_marker_is_expired() -> None:
    """With no network signal at all, a rejected login is read as a dead session."""
    with patch.object(session.wake, "_bff_login", return_value=(None, None)):
        ok, _, status = session.check(_ctx())

    assert ok is False
    assert status == session.STATUS_EXPIRED


def test_check_failed_login_with_recent_network_marker_is_net_error() -> None:
    """If the RENEWAL did not even start because of the network, the session may still be alive on the other side.

    Declaring it expired would bring up «Reautenticar» for no reason — the case behind the
    `red:` marker and its freshness window.
    """
    ctx = _ctx(refresh_reason="red:ConnectTimeout")

    with patch.object(session.wake, "_bff_login", return_value=(None, None)):
        ok, detalle, status = session.check(ctx)

    assert ok is False
    assert status == session.STATUS_NET_ERROR
    assert "ConnectTimeout" in detalle


def test_check_ignores_an_old_network_marker() -> None:
    """A marker older than `_REASON_FRESH_S` refers to another pass, not this one."""
    ctx = _ctx(
        refresh_reason="red:ConnectTimeout",
        refresh_ts=time.time() - session._REASON_FRESH_S - 1,
    )

    with patch.object(session.wake, "_bff_login", return_value=(None, None)):
        _, _, status = session.check(ctx)

    assert status == session.STATUS_EXPIRED


def test_check_recent_non_network_marker_is_expired() -> None:
    """Only `red:` means network. A `rechazado:*` is a real rejection → dead session."""
    ctx = _ctx(refresh_reason="rechazado:invalid_grant")

    with patch.object(session.wake, "_bff_login", return_value=(None, None)):
        _, _, status = session.check(ctx)

    assert status == session.STATUS_EXPIRED


def test_check_without_network_marker_declares_the_session_expired() -> None:
    """Without a RECENT «the renewal did not go out because of the network» marker, a failed login is a
    dead session and re-authentication is due.

    This test used to pass an empty `SimpleNamespace()` to exercise a `try/except` that
    tolerated contexts without `state`. Those contexts were the command-line diagnostic
    tools, deleted in the first pass: the guard no longer protected anything and, had it
    ever fired, it would have swallowed a real AttributeError."""
    from app.core.context import CoreCtx

    with patch.object(session.wake, "_bff_login", return_value=(None, None)):
        _, _, status = session.check(CoreCtx())

    assert status == session.STATUS_EXPIRED


# ───────────────────────── refresh() ─────────────────────────


@pytest.mark.parametrize(("devuelto", "esperado"), [(True, True), (None, False), (0, False)])
def test_refresh(devuelto, esperado) -> None:
    with patch.object(session.wake, "_refresh_token", return_value=devuelto):
        assert session.refresh(_ctx()) is esperado


def test_refresh_never_raises() -> None:
    with patch.object(session.wake, "_refresh_token", side_effect=RuntimeError("boom")):
        assert session.refresh(_ctx()) is False


# ───────────────────────── refresh_if_expiring() ─────────────────────────


def test_proactive_not_needed_yet() -> None:
    """At half its lifetime it is not renewed and NO call is made."""
    with (
        patch.object(session.wake, "_eta_token", return_value=(6 * 3600, 12 * 3600)),
        patch.object(session.wake, "_refresh_token_detail") as detalle,
    ):
        renewed, reason = session.refresh_if_expiring(_ctx())

    assert (renewed, reason) == (False, "no_hace_falta")
    detalle.assert_not_called()


def test_proactive_renews_past_the_quota() -> None:
    """Past the quota (0.8 = at 9h36m of 12h) it renews ahead of time."""
    with (
        patch.object(session.wake, "_eta_token", return_value=(10 * 3600, 12 * 3600)),
        patch.object(
            session.wake, "_refresh_token_detail", return_value=(True, "")
        ) as detalle,
    ):
        renewed, reason = session.refresh_if_expiring(_ctx())

    assert (renewed, reason) == (True, "")
    detalle.assert_called_once()


@pytest.mark.parametrize("eta_duracion", [(-1, 12 * 3600), (100, 0)])
def test_proactive_not_determinable(eta_duracion) -> None:
    """Without token.json (or with an impossible mtime) nothing is decided."""
    with patch.object(session.wake, "_eta_token", return_value=eta_duracion):
        assert session.refresh_if_expiring(_ctx()) == (False, "no_determinable")


def test_proactive_never_raises() -> None:
    """It is an optimisation: it cannot break the keep-alive that invokes it."""
    with patch.object(session.wake, "_eta_token", side_effect=OSError("stat")):
        renewed, reason = session.refresh_if_expiring(_ctx())

    assert renewed is False
    assert reason == "red:OSError"
