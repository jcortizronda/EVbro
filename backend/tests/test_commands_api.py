"""Sending commands: mandatory PIN for those that open the car, parameters and the PIN flow."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.api import routes_auth
from app.config import settings
from app.core import catalog, commands
from app.core.errors import CommandError
from app.db.models import AppUser, CommandAudit, EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import command_policy
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000001"
STORED_PIN = "1234"
PASSWORD = "una-contraseña-larga-1"
OPENING = {"desbloquear", "maletero_abrir", "ventanillas_abrir", "ventilar_ventanillas", "techo_abrir", "techo_ventilar", "alarma_off"}


class FakeSend:
    """Stands in for `commands.send`: never touches the network, records the PIN and parameters it was sent with."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def __call__(self, ctx, cmd_key, emit=None, params=None):
        self.calls.append({"key": cmd_key, "pin": ctx.pin, "params": params, "ctx": ctx})
        if self.error:
            raise self.error
        return f"{cmd_key}: HTTP 200 A00079 — comando aceptado"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, data_dir):
    monkeypatch.setattr(settings, "SETUP_CODE", "codigo-de-prueba")
    auth.reset_setup_code()
    for t in (routes_auth.ip_throttle, routes_auth.pair_throttle, command_policy.pin_throttle):
        t._fails.clear()
    VehiclePoller._contexts = {}
    yield
    VehiclePoller._contexts = {}
    auth.reset_setup_code()


@pytest.fixture
def send(monkeypatch) -> FakeSend:
    fake = FakeSend()
    monkeypatch.setattr(commands, "send", fake)

    async def _no_poll(self, vin, force=False):          # the poll after a success would hit the network
        return None
    monkeypatch.setattr(VehiclePoller, "poll_vehicle", _no_poll)
    return fake


class _FakeRequest:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "tests"}


async def prepare(db, *, pin=STORED_PIN) -> httpx.AsyncClient:
    """Vehicle + Ebro account (with or without a stored PIN) + EVBro user with the session already started.

    The client is returned NOT opened: each test uses it with `async with`."""
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        s.add(EbroAccount(phone="600123456", password="x", pin=pin, is_active=True, user_id=1))
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password(PASSWORD))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _FakeRequest())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    client.cookies.set(auth.COOKIE_NAME, token)
    return client


async def send_command(client, command, pin=None, params=None, vin=VIN):
    return await client.post(f"/api/vehicles/{vin}/commands", json={"command": command, "pin": pin, "params": params})


async def audit_rows() -> list[CommandAudit]:
    async with async_session_factory() as s:
        return list((await s.execute(select(CommandAudit).order_by(CommandAudit.id))).scalars())


# ───────────────────────── policy ─────────────────────────
def test_the_policy_only_names_commands_that_exist() -> None:
    assert command_policy.check_catalog_consistency() == []
    assert command_policy.PIN_REQUIRED == OPENING


def test_the_opening_commands_are_covered() -> None:
    """Any catalogue command whose body opens/unlocks/disarms must require the PIN."""
    by_body = {
        k for k, c in catalog.CMD_MAP.items()
        if (c["body"].get("lockType") == "1" or (c["endpoint"] if "endpoint" in c else "") in
            ("powerLiftgateControl", "windowControl", "skylightControl") and c["body"].get("controlType") in ("1", "2")
            or c.get("path", "").endswith("theftAlarm/setSwitch") and c["body"].get("theftAlarmSwitch") == "0")
    }
    assert by_body == command_policy.PIN_REQUIRED


async def test_the_catalogue_says_which_commands_ask_for_the_pin(db) -> None:
    client = await prepare(db)
    async with client:
        cat = (await client.get("/api/commands/catalog")).json()
    assert {c["key"] for c in cat if c["requires_pin"]} == OPENING
    assert len(cat) == len(catalog.COMMANDS)


# ───────────────────────── mandatory PIN ─────────────────────────
@pytest.mark.parametrize("command", sorted(OPENING))
async def test_without_pin_nothing_is_sent(db, send, command) -> None:
    client = await prepare(db)
    async with client:
        r = await send_command(client, command)                      # there is a stored PIN, but it is NOT enough
    assert r.status_code == 422 and r.json()["reason"] == "pin_required"
    assert send.calls == []


async def test_wrong_pin_is_rejected_locally_without_contacting_chery(db, send) -> None:
    client = await prepare(db)
    async with client:
        r = await send_command(client, "desbloquear", pin="9999")
    assert r.status_code == 403 and r.json()["reason"] == "pin_invalid"
    assert send.calls == []
    (row,) = await audit_rows()
    assert row.success is False and row.code == "pin_invalid"
    assert "9999" not in (row.message or "") and "9999" not in str(row.params)


async def test_correct_pin_sends_with_that_pin(db, send) -> None:
    client = await prepare(db)
    async with client:
        r = await send_command(client, "desbloquear", pin=STORED_PIN)
    assert r.status_code == 200 and r.json()["success"] is True
    assert [(c["key"], c["pin"]) for c in send.calls] == [("desbloquear", STORED_PIN)]


async def test_five_wrong_pins_lock_the_correct_one_too(db, send) -> None:
    client = await prepare(db)
    async with client:
        for _ in range(5):
            assert (await send_command(client, "desbloquear", pin="0000")).status_code == 403
        r = await send_command(client, "desbloquear", pin=STORED_PIN)
        assert r.status_code == 429 and r.json()["reason"] == "pin_locked" and int(r.headers["Retry-After"]) > 0
        command_policy.pin_throttle._fails.clear()
        assert (await send_command(client, "desbloquear", pin=STORED_PIN)).status_code == 200
    assert len(send.calls) == 1


async def test_a_correct_pin_resets_the_failures(db, send) -> None:
    client = await prepare(db)
    async with client:
        for _ in range(4):
            await send_command(client, "desbloquear", pin="0000")
        assert (await send_command(client, "desbloquear", pin=STORED_PIN)).status_code == 200
        for _ in range(4):
            assert (await send_command(client, "desbloquear", pin="0000")).status_code == 403   # does not reach the lock


async def test_a_non_opening_command_does_not_ask_for_the_pin_and_uses_the_stored_one(db, send) -> None:
    client = await prepare(db)
    async with client:
        for command in ("bloquear", "clima_on", "maletero_cerrar", "ventanillas_cerrar", "alarma_on", "carga_iniciar"):
            assert (await send_command(client, command)).status_code == 200, command
    assert {c["pin"] for c in send.calls} == {STORED_PIN}


# ───────────────────────── the typed PIN is not stored ─────────────────────────
async def test_the_typed_pin_is_only_valid_for_that_send(db, send) -> None:
    client = await prepare(db, pin=None)                      # no stored PIN
    async with client:
        r = await send_command(client, "bloquear", pin="9999")
        assert r.status_code == 200
        r = await send_command(client, "bloquear")                   # no PIN and nothing stored
    assert [c["pin"] for c in send.calls] == ["9999", ""]
    assert VehiclePoller.get_instance()._contexts[VIN].pin == ""


async def test_without_a_stored_pin_an_opening_command_forces_pin_verification(db, send) -> None:
    """The cached taskId does not prove the sender knows the PIN: it is dropped to force Chery to check."""
    client = await prepare(db, pin=None)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    ctx.state.taskid, ctx.state.taskid_ts = "taskid-en-cache", 9e12
    async with client:
        assert (await send_command(client, "desbloquear", pin="4321")).status_code == 200
    assert ctx.state.taskid is None
    assert send.calls[0]["pin"] == "4321"
    assert send.calls[0]["ctx"].state is ctx.state             # shares the vehicle's anti-lockout


async def test_with_a_stored_pin_the_cached_taskid_is_kept(db, send) -> None:
    client = await prepare(db)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    ctx.state.taskid, ctx.state.taskid_ts = "taskid-en-cache", 9e12
    async with client:
        assert (await send_command(client, "desbloquear", pin=STORED_PIN)).status_code == 200
    assert ctx.state.taskid == "taskid-en-cache"


# ───────────────────────── parameters ─────────────────────────
async def test_params_cannot_turn_a_free_command_into_an_opening_one(db, send) -> None:
    client = await prepare(db)
    async with client:
        for command, params in [("bloquear", {"lockType": "1"}),
                                ("maletero_cerrar", {"controlType": "1"}),
                                ("ventanillas_cerrar", {"controlType": "2"}),
                                ("alarma_on", {"theftAlarmSwitch": "0"}),
                                ("clima_on", {"vin": "OTRO", "taskId": "x"})]:
            r = await send_command(client, command, params=params)
            assert r.status_code == 422 and r.json()["reason"] == "params_invalid", (command, r.text)
    assert send.calls == []


async def test_temperature_and_duration_are_validated_and_normalised(db, send) -> None:
    client = await prepare(db)
    async with client:
        assert (await send_command(client, "clima_on", params={"temperature": "21,5", "times": 20})).status_code == 200
        for bad in ({"temperature": "99"}, {"temperature": "caliente"}, {"times": "0"}, {"times": "x"}):
            assert (await send_command(client, "clima_on", params=bad)).status_code == 422, bad
    assert [c["params"] for c in send.calls] == [{"temperature": "21.5", "times": "20"}]


# ───────────────────────── core errors ─────────────────────────
@pytest.mark.parametrize("reason,status", [("pin", 422), ("reauth", 424), ("config", 422), (None, 422)])
async def test_core_error_returns_status_and_reason(db, send, reason, status) -> None:
    send.error = CommandError("fallo de prueba", code="A00084", reason=reason)
    client = await prepare(db)
    async with client:
        r = await send_command(client, "bloquear")
    assert r.status_code == status and r.json()["reason"] == reason and r.json()["success"] is False
    assert "fallo de prueba" in r.json()["detail"]
    (row,) = await audit_rows()
    assert row.success is False and row.code == "A00084"


async def test_424_is_not_mistaken_for_an_expired_evbro_session(db, send) -> None:
    """The web shows the login on a 401; an expired Ebro session must not trigger it."""
    send.error = CommandError("Sesión caducada", reason="reauth")
    client = await prepare(db)
    async with client:
        assert (await send_command(client, "bloquear")).status_code != 401


async def test_unknown_vehicle_or_dangerous_path_gives_404(db, send) -> None:
    client = await prepare(db)
    async with client:
        assert (await send_command(client, "bloquear", vin="LSJA9999999999999")).status_code == 404
        assert (await send_command(client, "bloquear", vin="%2e%2e")).status_code == 404
    assert send.calls == []


def test_vehicle_data_dir_does_not_allow_escaping_data_dir(data_dir) -> None:
    from app.services.accounts import vehicle_data_dir
    for bad in ("..", "../x", "a/b", "", "ab"):
        with pytest.raises(ValueError):
            vehicle_data_dir(bad)
    assert vehicle_data_dir(VIN).startswith(str(data_dir))


# ───────────────────────── account PIN flow ─────────────────────────
async def test_changing_the_pin_resets_the_anti_lockout_and_the_taskid(db, send) -> None:
    client = await prepare(db)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    for _ in range(2):
        with ctx.lockout.attempt() as attempt:
            attempt.record_failure()
    ctx.state.taskid = "viejo"
    assert ctx.lockout.is_locked() is True
    async with client:
        r = await client.post("/api/account/pin", json={"pin": "5678"})
    assert r.status_code == 200
    assert ctx.lockout.is_locked() is False and ctx.state.taskid is None and ctx.pin == "5678"
    async with async_session_factory() as s:
        assert (await s.execute(select(EbroAccount.pin))).scalar_one() == "5678"


async def test_deleting_the_pin_empties_the_context_one(db, send) -> None:
    client = await prepare(db)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    ctx.pin = STORED_PIN
    async with client:
        assert (await client.post("/api/account/pin", json={"pin": ""})).status_code == 200
    assert ctx.pin == ""


async def test_disconnecting_the_ebro_account_wipes_the_pin_from_memory(db, send) -> None:
    client = await prepare(db)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    ctx.pin = STORED_PIN
    async with client:
        assert (await client.post("/api/account/logout")).status_code == 200
    assert ctx.pin == ""


# ───────────────────────── when the PIN is asked: the manager's choice ─────────────────────────
async def _set_policy(client, policy):
    return await client.put("/api/account/pin-policy", json={"policy": policy})


async def test_the_always_policy_asks_for_the_pin_also_to_switch_on(db, send) -> None:
    client = await prepare(db)
    async with client:
        assert (await _set_policy(client, "always")).status_code == 200
        r = await send_command(client, "clima_on")
        assert r.status_code == 422 and r.json()["reason"] == "pin_required" and send.calls == []
        assert (await send_command(client, "clima_on", pin=STORED_PIN)).status_code == 200
        cat = (await client.get("/api/commands/catalog")).json()
        assert all(c["requires_pin"] for c in cat)
        assert (await client.get("/api/account/status")).json()["pin_policy"] == "always"


async def test_the_never_policy_opens_with_the_stored_pin_without_typing_it(db, send) -> None:
    client = await prepare(db)
    async with client:
        assert (await _set_policy(client, "never")).status_code == 200
        for command in ("desbloquear", "maletero_abrir", "alarma_off"):
            assert (await send_command(client, command)).status_code == 200, command
        assert {c["pin"] for c in send.calls} == {STORED_PIN}
        assert not any(c["requires_pin"] for c in (await client.get("/api/commands/catalog")).json())
        # back to the default: opening asks again
        assert (await _set_policy(client, "open")).status_code == 200
        assert (await send_command(client, "desbloquear")).json()["reason"] == "pin_required"


async def test_never_requires_a_stored_pin_and_odd_values_are_rejected(db, send) -> None:
    client = await prepare(db, pin=None)
    async with client:
        r = await _set_policy(client, "never")
        assert r.status_code == 409 and "PIN guardado" in r.json()["detail"]
        assert (await _set_policy(client, "sometimes")).status_code == 422
        assert (await client.get("/api/account/status")).json()["pin_policy"] == "open"


async def test_the_alarm_of_a_car_without_an_ebro_session_is_unknown_not_a_server_error(db) -> None:
    """The page asks for the alarm every minute; a car whose account was never connected has no session file (09-11-2026: 500 and a
    traceback in the log each time)."""
    client = await prepare(db)
    try:
        r = await client.get(f"/api/vehicles/{VIN}/alarm")
        assert r.status_code == 200 and r.json() == {"enabled": None}
    finally:
        await client.aclose()


# ───────────────────────── orders captured from the official app on 2026-11-21 ─────────────────────────
def test_the_bodies_of_the_new_orders_are_the_ones_the_official_app_sends() -> None:
    """Purifier and demisting are fields of the climate order; quick heating is one order for everything; tilting the roof is
    the third value of the roof order. Each body is what the official app sent and the car obeyed (an S900)."""
    body = lambda key: (catalog.CMD_MAP[key]["endpoint"], catalog.CMD_MAP[key]["body"])        # noqa: E731
    climate = {"airControlType": "1", "airType": "1", "temperature": "21.0", "times": "15"}
    assert body("purificador_on") == ("airControl", {**climate, "airPurControlType": "1"})
    assert body("purificador_off") == ("airControl", {**climate, "airPurControlType": "0"})          # the climate stays on
    assert body("desempanar_on") == ("airControl", {**climate, "frontDefrosting": "1"})
    assert body("desempanar_off") == ("airControl", {**climate, "frontDefrosting": "0"})
    heat = {"airControlType": "1", "airType": "1", "temperature": "30", "duration": "15", "mSeatHeating": "3", "pSeatHeating": "3",
            "blSeatHeating": "3", "brSeatHeating": "3", "steerWheelHeatSwitch": "1", "frontWindshieldHeat": "1"}
    assert body("calefaccion_rapida") == ("heatingControl", heat)
    assert body("calefaccion_rapida_off") == ("heatingControl", {k: (v if k in ("airType", "temperature", "duration") else "0") for k, v in heat.items()})
    assert body("techo_ventilar") == ("skylightControl", {"controlType": "2", "skylightType": "1"})
    assert body("salida_prog_on") == ("appointmentTravel", {"mainSwitcher": 1, "appointmentTravelAOS": [
        {"switcher": 1, "travelTime": 420, "travelWeekday": [1, 2, 3, 4, 5, 6, 7]}]})
    # the heated glass keeps its key and its order; only its name changed (it was called «Desempañar parabrisas»)
    assert body("defrost_parabrisas") == ("frontWindshieldControl", {"frontWindshieldHeat": "1", "times": "15"})
    assert catalog.CMD_MAP["defrost_parabrisas"]["name"] == "Parabrisas térmico"


async def test_the_purifier_and_the_demisting_carry_the_climate_temperature_and_nothing_else(db, send) -> None:
    """They travel in the climate order: without the temperature the car has set, turning the purifier off would move the
    climate to the catalogue's default. No other field of that order can be slipped in."""
    client = await prepare(db)
    async with client:
        for command in ("purificador_on", "purificador_off", "desempanar_on", "desempanar_off"):
            assert (await send_command(client, command, params={"temperature": "23.0"})).status_code == 200, command
        for command, params in [("purificador_off", {"airControlType": "0"}), ("desempanar_on", {"frontDefrosting": "0"}),
                                ("purificador_on", {"temperature": "40"}), ("calefaccion_rapida", {"temperature": "20"}),
                                ("techo_ventilar", {"controlType": "1"}), ("salida_prog_on", {"mainSwitcher": 0})]:
            r = await send_command(client, command, pin=STORED_PIN, params=params)
            assert r.status_code == 422 and r.json()["reason"] == "params_invalid", (command, r.text)
    assert [(c["key"], c["params"]) for c in send.calls] == [(k, {"temperature": "23.0"}) for k in
                                                             ("purificador_on", "purificador_off", "desempanar_on", "desempanar_off")]


async def test_tilting_the_roof_asks_for_the_pin_like_opening_it(db, send) -> None:
    client = await prepare(db)
    async with client:
        r = await send_command(client, "techo_ventilar")
        assert r.status_code == 422 and r.json()["reason"] == "pin_required" and send.calls == []
        assert (await send_command(client, "techo_ventilar", pin=STORED_PIN)).status_code == 200
        assert (await send_command(client, "calefaccion_rapida")).status_code == 200                 # heating opens nothing: no PIN
    assert [c["key"] for c in send.calls] == ["techo_ventilar", "calefaccion_rapida"]
