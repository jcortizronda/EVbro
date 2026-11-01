"""Tests for `core/wake.py` — token, BFF login and wake orchestration.

Strategy: the response parsers are pure and tested directly; for the rest the
highest-level seam possible is mocked (`ebro_login.refresh_token` instead of
`requests`), which is the one the module itself treats as its boundary.
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest

from app.core import wake
from app.core.context import CoreCtx


@pytest.fixture
def ctx(tmp_path) -> CoreCtx:
    """Context with the paths inside `tmp_path`.

    Without this it would write to `core/token.json`, INSIDE the installed package.
    """
    return CoreCtx(
        vin="LSJA0000000000001",
        token_path=str(tmp_path / "token.json"),
        taskid_file=str(tmp_path / "taskid.txt"),
    )


def _escribe_token(ctx: CoreCtx, **campos) -> None:
    payload = {"access_token": "AT", "refresh_token": "RT", "expires_in": 43200, **campos}
    with open(ctx.token_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


# ───────────────────────── pure parsers ─────────────────────────


@pytest.mark.parametrize(
    ("j", "esperado"),
    [
        ({"code": "000000"}, "000000"),
        ({}, None),
        ("no es un dict", "no es un dict"),
        (None, None),
    ],
)
def test_code_of(j, esperado) -> None:
    assert wake._code_of(j) == esperado


def test_payload_prefers_data_over_body() -> None:
    assert wake._payload({"data": {"a": 1}, "body": {"b": 2}}) == {"a": 1}


def test_payload_falls_back_to_body() -> None:
    """`/asr/manager/realtime` answers under `body`: without this branch the 84 fields were lost."""
    assert wake._payload({"body": {"dumpEnergy": "80"}}) == {"dumpEnergy": "80"}


@pytest.mark.parametrize(
    "j",
    [
        {},
        {"data": {}},  # empty dict = no useful payload
        {"data": None},
        {"data": "texto"},  # no-dict
        "no es un dict",
        None,
    ],
)
def test_payload_without_data(j) -> None:
    assert wake._payload(j) is None
    assert wake._has_live_data(j) is False


def test_has_live_data() -> None:
    assert wake._has_live_data({"body": {"x": 1}}) is True


def test_fingerprint_is_stable_sha256() -> None:
    """The fingerprint is compared, never the plain credential."""
    h = wake._fingerprint("RT")
    assert h == wake._fingerprint("RT")
    assert h != wake._fingerprint("OTRO")
    assert len(h) == 64
    assert "RT" not in h


# ───────────────────────── _access_token ─────────────────────────


def test_access_token_from_the_root(ctx: CoreCtx) -> None:
    _escribe_token(ctx)
    assert wake._access_token(ctx) == "AT"


def test_access_token_from_data(ctx: CoreCtx) -> None:
    with open(ctx.token_path, "w", encoding="utf-8") as fh:
        json.dump({"data": {"access_token": "AT-anidado"}}, fh)

    assert wake._access_token(ctx) == "AT-anidado"


def test_access_token_non_dict_json(ctx: CoreCtx) -> None:
    with open(ctx.token_path, "w", encoding="utf-8") as fh:
        json.dump(["no", "es", "un", "dict"], fh)

    assert wake._access_token(ctx) is None


# ───────────────────────── _eta_token ─────────────────────────


def test_eta_token_without_file(ctx: CoreCtx) -> None:
    assert wake._eta_token(ctx) == (-1.0, 0)


def test_eta_token_age_from_the_mtime(ctx: CoreCtx, freezer: FrozenDateTimeFactory) -> None:
    """The token is opaque (not a JWT): the age is measured on the file's mtime, which is
    rewritten on every renewal."""
    _escribe_token(ctx, expires_in=43200)
    freezer.tick(3600)

    eta, lifetime = wake._eta_token(ctx)

    assert lifetime == 43200
    assert eta == pytest.approx(3600, abs=5)


def test_eta_token_unreadable_expires_in(ctx: CoreCtx) -> None:
    _escribe_token(ctx, expires_in="no-un-numero")
    assert wake._eta_token(ctx)[1] == 0


# ───────────────────────── _refresh_token_detail ─────────────────────────


def test_refresh_without_file_is_ausente(ctx: CoreCtx) -> None:
    ok, reason = wake._refresh_token_detail(ctx)

    assert (ok, reason) == (False, "ausente")
    assert ctx.state.refresh_reason == "ausente"


def test_refresh_without_refresh_token_is_ausente(ctx: CoreCtx) -> None:
    with open(ctx.token_path, "w", encoding="utf-8") as fh:
        json.dump({"access_token": "AT"}, fh)

    assert wake._refresh_token_detail(ctx) == (False, "ausente")


def test_refresh_ok_rewrites_the_token_atomically(ctx: CoreCtx) -> None:
    _escribe_token(ctx)
    nuevo = {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 43200}

    with patch(
        "app.core.ebro_login.refresh_token",
        return_value=(True, {"raw": nuevo}),
    ):
        ok, reason = wake._refresh_token_detail(ctx)

    assert (ok, reason) == (True, "")
    with open(ctx.token_path, encoding="utf-8") as fh:
        assert json.load(fh) == nuevo
    # the atomic write's temp file must not be left lying around
    assert not os.path.exists(ctx.token_path + ".tmp")
    # the token is a credential: readable by the owner only
    assert oct(os.stat(ctx.token_path).st_mode)[-3:] == "600"


def test_refresh_network_error_is_not_a_revocation(ctx: CoreCtx) -> None:
    """The distinction is the whole point of the function: without it an unstable connection would
    bring up «Reautenticar» and the user would re-authenticate for nothing."""
    _escribe_token(ctx)

    with patch(
        "app.core.ebro_login.refresh_token",
        side_effect=OSError("timeout"),
    ):
        ok, reason = wake._refresh_token_detail(ctx)

    assert ok is False
    assert reason == "red:OSError"
    assert ctx.state.refresh_burned == ""  # the brake is NOT armed by a network failure


def test_refresh_rejected_arms_the_brake(ctx: CoreCtx) -> None:
    """A revoked refresh_token does not become valid again on its own: retrying it on every call is
    just noise towards the gateway (measured: 5 identical attempts in 6 minutes)."""
    _escribe_token(ctx)

    with patch(
        "app.core.ebro_login.refresh_token",
        return_value=(False, "invalid_grant"),
    ) as refresh:
        ok, reason = wake._refresh_token_detail(ctx)

        assert ok is False
        assert reason.startswith("rechazado:")
        assert ctx.state.refresh_burned == wake._fingerprint("RT")

        # second attempt with the SAME token: the server is not even called
        refresh.reset_mock()
        ok2, motivo2 = wake._refresh_token_detail(ctx)

    assert (ok2, motivo2) == (False, "rechazado:ya_rechazado")
    refresh.assert_not_called()


def test_brake_releases_with_a_different_refresh_token(ctx: CoreCtx) -> None:
    """The brake releases on its own as soon as the file holds another credential."""
    _escribe_token(ctx)
    ctx.state.refresh_burned = wake._fingerprint("RT")
    ctx.state.refresh_burned_ts = 1e12  # very recent

    _escribe_token(ctx, refresh_token="RT-NUEVO")

    with patch(
        "app.core.ebro_login.refresh_token",
        return_value=(True, {"raw": {"access_token": "AT3"}}),
    ) as refresh:
        ok, _ = wake._refresh_token_detail(ctx)

    assert ok is True
    refresh.assert_called_once()


def test_brake_expires_after_the_retry_window(
    ctx: CoreCtx, freezer: FrozenDateTimeFactory
) -> None:
    """It is retried once every `retry_refresh_after_s` in case the rejection was a server
    hiccup: staying blocked forever would be worse."""
    _escribe_token(ctx)
    ctx.state.refresh_burned = wake._fingerprint("RT")
    ctx.state.refresh_burned_ts = __import__("time").time()

    freezer.tick(ctx.retry_refresh_after_s + 1)

    with patch(
        "app.core.ebro_login.refresh_token",
        return_value=(True, {"raw": {"access_token": "AT4"}}),
    ) as refresh:
        wake._refresh_token_detail(ctx)

    refresh.assert_called_once()


def test_double_check_detects_another_thread_already_renewed(ctx: CoreCtx) -> None:
    """Redoing the refresh would burn the new token and invalidate the whole session."""
    _escribe_token(ctx)

    real_open = open
    llamadas = {"n": 0}

    def _open_que_cambia(path, *args, **kwargs):
        # the 2nd read (the one inside the lock) already sees the token renewed by another thread
        if path == ctx.token_path and "r" in (args[0] if args else kwargs.get("mode", "r")):
            llamadas["n"] += 1
            if llamadas["n"] == 2:
                with real_open(path, "w", encoding="utf-8") as fh:
                    json.dump({"access_token": "AT-OTRO-HILO", "refresh_token": "RT"}, fh)
        return real_open(path, *args, **kwargs)

    with (
        patch("builtins.open", _open_que_cambia),
        patch("app.core.ebro_login.refresh_token") as refresh,
    ):
        ok, reason = wake._refresh_token_detail(ctx)

    assert (ok, reason) == (True, "")
    refresh.assert_not_called()


def test_refresh_token_compat_returns_bool(ctx: CoreCtx) -> None:
    assert wake._refresh_token(ctx) is False


# ───────────────────────── do_wake ─────────────────────────


def test_do_wake_never_raises(ctx: CoreCtx) -> None:
    """Every error becomes a readable status; the button must never blow up."""
    publicados: list[str] = []

    with patch.object(wake, "_do_wake_inner", side_effect=RuntimeError("boom")):
        res = wake.do_wake(ctx, publicados.append)

    assert res["ok"] is False
    assert res["reason"] == "exception"
    assert any("Error al despertar" in p for p in publicados)


def test_do_wake_is_one_at_a_time_per_vehicle(ctx: CoreCtx) -> None:
    """Two cars can be woken in parallel; the same one cannot."""
    publicados: list[str] = []
    ctx.state.wake_lock.acquire()
    try:
        res = wake.do_wake(ctx, publicados.append)
    finally:
        ctx.state.wake_lock.release()

    assert res == {"ok": False, "reason": "busy"}
    assert any("ya en curso" in p for p in publicados)


def test_do_wake_releases_the_lock_even_on_failure(ctx: CoreCtx) -> None:
    with patch.object(wake, "_do_wake_inner", side_effect=RuntimeError("boom")):
        wake.do_wake(ctx, lambda _m: None)

    assert ctx.state.wake_lock.acquire(blocking=False) is True
    ctx.state.wake_lock.release()


def test_pacing_is_configured_per_context(ctx: CoreCtx) -> None:
    """The cooldown and the number of cycles belong to the VEHICLE, not the process.

    They used to be module constants read from `os.environ` at import, and this test merely
    documented that limitation (`monkeypatch.setenv` did not work). Now the behaviour can be
    checked: lowering the cooldown to zero makes the wake not honour it."""
    ctx.state.last_sms_ts = time.time()   # an SMS was just sent

    ctx_con_cooldown = replace_cooldown(ctx, 300)
    res = wake.do_wake(ctx_con_cooldown, lambda _m: None, is_awake=lambda: False)
    assert res["reason"] == "cooldown"

    ctx_sin_cooldown = replace_cooldown(ctx, 0)
    with patch.object(wake, "_bff_login", return_value=(None, None)):
        res = wake.do_wake(ctx_sin_cooldown, lambda _m: None, is_awake=lambda: False)
    assert res["reason"] == "no_usertoken"   # it got past the cooldown and reached the login


def replace_cooldown(ctx: CoreCtx, segundos: int) -> CoreCtx:
    """The same context with another cooldown, keeping the state (which is what holds the
    last SMS timestamp)."""
    import dataclasses

    return dataclasses.replace(ctx, wake_cooldown_s=segundos, state=ctx.state)
