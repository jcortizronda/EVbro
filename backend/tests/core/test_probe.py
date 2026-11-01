"""Tests for `core/probe.py` — the read-only probe of the realtime channel.

`probe.W._bff_login` and `probe.W._signed_post` are mocked (`W` is the alias of `wake` inside
the module): it is the boundary the module's own self-test already treats as a seam.
The probe's raw log is empty by default in `CoreCtx`, so no file is written.
"""

from __future__ import annotations

import json
import time
from unittest.mock import Mock, patch

import pytest
from syrupy.assertion import SnapshotAssertion

from app.core import probe
from app.core.context import CoreCtx

REALTIME_OK = {
    "code": "000000",
    "body": {
        # NO lat/lon on purpose: the app's real capture shows `/asr/manager/realtime`
        # returns 80 fields and not one coordinate. Putting them here faked a backend that does not
        # exist, and a wrong explanation of a real fault was built on that fiction.
        "dumpEnergy": "64.5",
        "vehicleSpeed": "0",
        "odometer": "12345",
        "pureElectricRange": "60",
        "chargeState": "1",
        "onlineStatus": "1",
    },
}
DORMIDO = {"code": "A07900"}

LOCATION_FRESCA = {
    "code": "000000",
    "data": {"lat": "41.385064", "lon": "2.173404", "direction": "90"},
}


@pytest.fixture
def ctx() -> CoreCtx:
    return CoreCtx(vin="LSJA0000000000001")


def _post(por_path: dict):
    """Build a `_signed_post` that answers according to the requested path."""

    def _fake(_ctx, _ut, path, _params):
        for fragmento, respuesta in por_path.items():
            if fragmento in path:
                return 200, respuesta
        return 200, DORMIDO

    return _fake


# ───────────────────────── _rich (pure) ─────────────────────────


def test_rich_excluye_la_posicion(snapshot: SnapshotAssertion) -> None:
    """`probe_status` ends up in a sensor's state (and therefore in HA's database),
    in «Descargar diagnóstico» and in the log. Writing lat/lon there leaked in plain text where the
    car is in all three places — found in the field on 2026-07-20.
    """
    rich = probe._rich(REALTIME_OK["body"])

    assert not (set(rich) & set(probe._GEO_KEYS))
    assert rich == snapshot


def test_rich_keeps_what_is_useful_for_diagnostics() -> None:
    rich = probe._rich(REALTIME_OK["body"])
    assert rich["odometer"] == "12345"
    assert rich["dumpEnergy"] == "64.5"
    assert rich["onlineStatus"] == "1"


@pytest.mark.parametrize("data", [{}, {"campo": "sin interés"}, None, "texto"])
def test_rich_tolerates_odd_input(data) -> None:
    assert probe._rich(data) == {}


def test_geo_keys_are_a_subset_of_rich_keys() -> None:
    assert set(probe._GEO_KEYS) <= set(probe.RICH_KEYS)
    assert set(probe._MSG_KEYS) == set(probe.RICH_KEYS) - set(probe._GEO_KEYS)


# ───────────────────────── probe_once ─────────────────────────


def test_probe_once_with_live_data(ctx: CoreCtx) -> None:
    publicados: list[str] = []
    on_data = Mock()

    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post",
                     _post({"realtime": REALTIME_OK, "queryVehicleLocation": LOCATION_FRESCA})),
    ):
        res = probe.probe_once(ctx, publicados.append, force=True, on_data=on_data)

    assert res["ok"] is True
    assert res["online"] is True
    assert res["got_data"] is True

    # `on_data` receives the RAW dict: the position travels intact to the device_tracker even though
    # it is excluded from the readable message.
    on_data.assert_called_once()
    crudo = on_data.call_args[0][0]
    assert crudo["lat"] == "41.385064"

    # ...and does NOT appear in any published text
    assert not any("41.385064" in p for p in publicados)


def test_probe_once_car_asleep(ctx: CoreCtx) -> None:
    publicados: list[str] = []
    on_data = Mock()

    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post", _post({})),
    ):
        res = probe.probe_once(ctx, publicados.append, force=True, on_data=on_data)

    assert res["ok"] is True
    assert res["online"] is False
    assert res["got_data"] is False
    assert res["codes"] == ["A07900", "A07900", "A07900"]
    on_data.assert_not_called()


def test_probe_once_returns_the_per_endpoint_breakdown(ctx: CoreCtx) -> None:
    """The aggregated summary (`got_data`) cannot tell «nobody answered» from «telemetry
    answered but location did not». The diagnostic monitor needs that difference, so
    the probe has to RETURN it: `_log`'s JSONL log depends on an environment variable that
    nobody sets on a normal Home Assistant install."""
    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post", _post({"realtime": REALTIME_OK})),
    ):
        res = probe.probe_once(ctx, lambda _m: None, force=True)

    assert res["got_realtime"] is True
    assert res["got_location"] is False          # location did not answer...
    assert res["got_data"] is True               # ...but telemetry did
    assert res["has_position"] is False
    assert res["location_code"] == "A07900"
    assert isinstance(res["duration_ms"], int)


def test_breakdown_carries_no_coordinates(ctx: CoreCtx) -> None:
    """`has_position` is a boolean on purpose: this dict ends up in the diagnostics file,
    which is shared with third parties. The position travels to the device_tracker via `on_data`."""
    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post",
                     _post({"realtime": REALTIME_OK, "queryVehicleLocation": LOCATION_FRESCA})),
    ):
        res = probe.probe_once(ctx, lambda _m: None, force=True, on_data=Mock())

    assert res["has_position"] is True
    assert "lat" not in res and "lon" not in res
    assert "41.385064" not in json.dumps(res, default=str)


def test_probe_once_expired_session(ctx: CoreCtx) -> None:
    publicados: list[str] = []

    with patch.object(probe.W, "_bff_login", return_value=(None, None)):
        res = probe.probe_once(ctx, publicados.append, force=True)

    assert res == {"ok": False, "reason": "no_usertoken"}
    assert any("sesión caducada" in p.lower() for p in publicados)


def test_probe_once_honours_the_cooldown(ctx: CoreCtx) -> None:
    """The cooldown is PER VEHICLE (it lives in the context): with two cars, one car's probe no
    longer consumes the other's."""
    ctx.state.last_probe_ts = time.time()

    with patch.object(probe.W, "_bff_login") as login:
        res = probe.probe_once(ctx, lambda _m: None)

    assert res["reason"] == "cooldown"
    assert res["wait_s"] <= ctx.probe_cooldown_s
    login.assert_not_called()


def test_cooldown_is_announced_instead_of_returning_silently(ctx: CoreCtx) -> None:
    """A silent return was indistinguishable from a broken integration: whoever pressed «Actualizar
    ubicación» saw neither a message nor a state change."""
    ctx.state.last_probe_ts = time.time()
    publicados: list[str] = []

    with patch.object(probe.W, "_bff_login"):
        probe.probe_once(ctx, publicados.append)

    assert any("espero" in m and "s" in m for m in publicados)


def test_probe_once_force_ignores_the_cooldown(ctx: CoreCtx) -> None:
    ctx.state.last_probe_ts = time.time()

    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")) as login,
        patch.object(probe.W, "_signed_post", _post({"realtime": REALTIME_OK})),
    ):
        probe.probe_once(ctx, lambda _m: None, force=True)

    login.assert_called_once()


def test_probe_once_one_at_a_time(ctx: CoreCtx) -> None:
    ctx.state.probe_lock.acquire()
    try:
        res = probe.probe_once(ctx, lambda _m: None, force=True)
    finally:
        ctx.state.probe_lock.release()

    assert res == {"ok": False, "reason": "busy"}


def test_probe_once_never_raises(ctx: CoreCtx) -> None:
    publicados: list[str] = []

    with patch.object(probe.W, "_bff_login", side_effect=RuntimeError("boom")):
        res = probe.probe_once(ctx, publicados.append, force=True)

    assert res["ok"] is False
    assert res["reason"] == "exception"
    assert any("Error de sonda" in p for p in publicados)
    # and the lock is released
    assert ctx.state.probe_lock.acquire(blocking=False) is True
    ctx.state.probe_lock.release()


def test_probe_once_broken_on_data_does_not_break_the_probe(ctx: CoreCtx) -> None:
    publicados: list[str] = []

    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post", _post({"realtime": REALTIME_OK})),
    ):
        res = probe.probe_once(
            ctx, publicados.append, force=True, on_data=Mock(side_effect=ValueError("mal"))
        )

    assert res["ok"] is True
    assert any("error al publicar datos" in p.lower() for p in publicados)


def test_probe_once_merges_the_three_endpoints(ctx: CoreCtx) -> None:
    """Realtime has priority; location and travel add extra fields."""
    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(
            probe.W,
            "_signed_post",
            _post(
                {
                    "realtime": REALTIME_OK,
                    "queryVehicleLocation": {"code": "000000", "data": {"gpsTime": "123"}},
                    "travelQuery": {"code": "000000", "data": {"totalKm": "999"}},
                }
            ),
        ),
    ):
        on_data = Mock()
        probe.probe_once(ctx, lambda _m: None, force=True, on_data=on_data)

    crudo = on_data.call_args[0][0]
    assert crudo["gpsTime"] == "123"  # from location
    assert crudo["totalKm"] == "999"  # from travel
    assert crudo["dumpEnergy"] == "64.5"  # from realtime (takes priority)


# ───────────────────── how old the probe data is ─────────────────────


def test_freshness_with_the_car_awake() -> None:
    """`onlineStatus=1`: the car answered, the data is from right now."""
    assert "En vivo" in probe.freshness({"onlineStatus": "1", "time": "1000"}, now=99999)


def test_freshness_with_the_car_asleep_states_the_age() -> None:
    """The misleading case: the endpoint answers just as well, but returns the last
    snapshot the cloud stored. Announcing it as «realtime with the car awake»
    sent people looking for the fault where it was not."""
    ahora = 1_787_092_000.0
    hace_26_min = str(int((ahora - 26 * 60) * 1000))

    mensaje = probe.freshness({"onlineStatus": "0", "time": hace_26_min}, now=ahora)

    assert "26 min" in mensaje
    assert "dormido" in mensaje
    assert "En vivo" not in mensaje


def test_freshness_without_timestamp_does_not_invent_one() -> None:
    mensaje = probe.freshness({"onlineStatus": "0"}, now=1_787_092_000.0)

    assert "dormido" in mensaje
    assert "min" not in mensaje


def test_freshness_falls_back_to_result_time_without_time() -> None:
    """`time` is the good timestamp, but it does not always come."""
    ahora = 1_787_092_000.0
    hace_5_min = str(int((ahora - 5 * 60) * 1000))

    assert "5 min" in probe.freshness({"onlineStatus": "0", "resultTime": hace_5_min}, now=ahora)


# ───────────────── where the POSITION comes from ─────────────────
# `queryVehicleLocation` is the only source: the `/asr/manager/realtime` response carries NO
# coordinates —verified on a real capture of the official app, 80 fields and none of them—. And it is
# a QUERY: it returns the last position known to the cloud without asking the car anything.
# What forces a new fix is the `vehicleLocation` command, which goes with a taskId.

def _sonda(ctx: CoreCtx, respuestas: dict) -> dict:
    recibido: dict = {}
    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post", _post(respuestas)),
    ):
        probe.probe_once(ctx, lambda _m: None, force=True, on_data=recibido.update)
    return recibido


def test_position_comes_from_the_location_endpoint(ctx: CoreCtx) -> None:
    """And only from there: this capture's realtime carries no coordinates."""
    datos = _sonda(ctx, {"queryVehicleLocation": LOCATION_FRESCA, "realtime": REALTIME_OK})

    assert datos["lat"] == "41.385064"
    assert datos["lon"] == "2.173404"


def test_realtime_wins_for_telemetry(ctx: CoreCtx) -> None:
    """Where both responses overlap realtime wins, as it goes last in the merge."""
    datos = _sonda(ctx, {"queryVehicleLocation": LOCATION_FRESCA, "realtime": REALTIME_OK})

    assert datos["dumpEnergy"] == "64.5"
    assert datos["odometer"] == "12345"


def test_no_location_response_means_no_position(ctx: CoreCtx) -> None:
    """If the location endpoint does not answer, the probe has no coordinates to give —
    there is no second source to get them from."""
    datos = _sonda(ctx, {"queryVehicleLocation": DORMIDO, "realtime": REALTIME_OK})

    assert "lat" not in datos
    assert datos["dumpEnergy"] == "64.5"


def test_probe_does_not_claim_the_car_is_awake_before_asking(
    ctx: CoreCtx,
) -> None:
    """The opening message said «the car is awake» BEFORE the first request, and
    two lines later the response concluded it was asleep. The one who knows is `freshness()`, with the
    response's `onlineStatus` in front of it."""
    dormido = {"code": "000000", "body": {**REALTIME_OK["body"], "onlineStatus": "0"}}
    publicados: list[str] = []

    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post",
                     _post({"realtime": dormido, "queryVehicleLocation": LOCATION_FRESCA})),
    ):
        probe.probe_once(ctx, publicados.append, force=True)

    assert not any("está despierto" in m for m in publicados)
    assert any("dormido" in m for m in publicados)


def test_result_does_not_repeat_the_sensors(ctx: CoreCtx) -> None:
    """The state is published on a sensor and read on a card. Dumping speed, odometer and
    battery there made it very long just to repeat what the user already has in their entities; those
    fields still go to the diagnostic report and the probe log, which is where they are useful."""
    publicados: list[str] = []

    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post",
                     _post({"realtime": REALTIME_OK, "queryVehicleLocation": LOCATION_FRESCA})),
    ):
        res = probe.probe_once(ctx, publicados.append, force=True)

    assert not any("odometer" in m or "dumpEnergy" in m for m in publicados)
    assert max(len(m) for m in publicados) < 70      # fits on a card without being cut
    assert res["rich"]["odometer"] == "12345"        # but diagnostics keep them


def test_probe_warns_when_there_is_no_position(ctx: CoreCtx) -> None:
    """The fault that cost four days of a stuck map with nothing saying so: if `realtime`
    answers and the location endpoint does not, the probe calmly published «🟢 En vivo».

    This sensor is called «Resultado sonda de UBICACIÓN»: when there is no position, that is what
    it has to report, and with the reason — which we already had in the endpoint code and threw away."""
    publicados: list[str] = []

    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post",
                     _post({"realtime": REALTIME_OK, "queryVehicleLocation": DORMIDO})),
    ):
        probe.probe_once(ctx, publicados.append, force=True)

    # «Con datos» because telemetry DID arrive: saying just «sin posición» would suggest
    # the whole query failed. And without the reason: the only one that shows up in practice is
    # «coche en reposo», which next to «coche dormido» looks like two things when they are the same.
    assert "🟠 Con datos, sin posición" in publicados
    assert not any("En vivo" in m for m in publicados)


def test_with_position_the_message_is_the_freshness_one(ctx: CoreCtx) -> None:
    publicados: list[str] = []

    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post",
                     _post({"realtime": REALTIME_OK, "queryVehicleLocation": LOCATION_FRESCA})),
    ):
        probe.probe_once(ctx, publicados.append, force=True)

    assert any("En vivo" in m for m in publicados)
    assert not any("sin posición" in m for m in publicados)


def test_state_colours_distinguish_severity(ctx: CoreCtx) -> None:
    """The card colours the refresh button by the state's emoji, so the emoji IS
    the signal: 🔴 nothing at all, 🟠 data but no position. Both used to be 🟡 and on the
    card they looked equally serious."""
    sin_nada: list[str] = []
    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post", _post({})),      # everything answers DORMIDO
    ):
        probe.probe_once(ctx, sin_nada.append, force=True)

    a_medias: list[str] = []
    with (
        patch.object(probe.W, "_bff_login", return_value=("UT", "TU")),
        patch.object(probe.W, "_signed_post",
                     _post({"realtime": REALTIME_OK, "queryVehicleLocation": DORMIDO})),
    ):
        probe.probe_once(ctx, a_medias.append, force=True)

    assert any(m.startswith("🔴") for m in sin_nada)
    assert any(m.startswith("🟠") for m in a_medias)
