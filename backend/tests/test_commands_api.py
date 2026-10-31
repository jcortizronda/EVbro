"""Envío de comandos: PIN obligatorio en los que abren el coche, parámetros y flujo del PIN."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.api import routes_auth
from app.config import settings
from app.core import catalog
from app.core.errors import CommandError
from app.db.models import AppUser, CommandAudit, EbroAccount, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import command_policy
from app.services import poller as poller_module
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000001"
STORED_PIN = "1234"
PASSWORD = "una-contraseña-larga-1"
ABREN = {"desbloquear", "maletero_abrir", "ventanillas_abrir", "ventilar_ventanillas", "techo_abrir", "alarma_off"}


class FakeSend:
    """Sustituye a `commands.send`: no toca la red, anota con qué PIN y parámetros se envió."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def __call__(self, ctx, cmd_key, emit=None, params=None):
        self.calls.append({"key": cmd_key, "pin": ctx.pin, "params": params, "ctx": ctx})
        if self.error:
            raise self.error
        return f"{cmd_key}: HTTP 200 A00079 — comando aceptado"


@pytest.fixture(autouse=True)
def _estado_limpio(monkeypatch, data_dir):
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
    monkeypatch.setattr(poller_module.commands, "send", fake)

    async def _sin_sondeo(self, vin, force=False):          # el sondeo posterior al éxito iría a la red
        return None
    monkeypatch.setattr(VehiclePoller, "poll_vehicle", _sin_sondeo)
    return fake


class _PeticionFalsa:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "tests"}


async def preparar(db, *, pin=STORED_PIN) -> httpx.AsyncClient:
    """Vehículo + cuenta Ebro (con o sin PIN guardado) + usuario de EVBro con la sesión ya iniciada.

    El cliente se devuelve SIN abrir: cada test lo usa con `async with`."""
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        s.add(EbroAccount(phone="600123456", password="x", pin=pin, is_active=True, user_id=1))
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password(PASSWORD))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _PeticionFalsa())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    client.cookies.set(auth.COOKIE_NAME, token)
    return client


async def enviar(client, command, pin=None, params=None, vin=VIN):
    return await client.post(f"/api/vehicles/{vin}/commands", json={"command": command, "pin": pin, "params": params})


async def auditoria() -> list[CommandAudit]:
    async with async_session_factory() as s:
        return list((await s.execute(select(CommandAudit).order_by(CommandAudit.id))).scalars())


# ───────────────────────── política ─────────────────────────
def test_la_politica_solo_nombra_comandos_que_existen() -> None:
    assert command_policy.check_catalog_consistency() == []
    assert command_policy.PIN_REQUIRED == ABREN


def test_los_comandos_que_abren_estan_cubiertos() -> None:
    """Cualquier comando del catálogo cuyo cuerpo abra/desbloquee/desarme debe exigir PIN."""
    por_cuerpo = {
        k for k, c in catalog.CMD_MAP.items()
        if (c["body"].get("lockType") == "1" or (c["endpoint"] if "endpoint" in c else "") in
            ("powerLiftgateControl", "windowControl", "skylightControl") and c["body"].get("controlType") in ("1", "2")
            or c.get("path", "").endswith("theftAlarm/setSwitch") and c["body"].get("theftAlarmSwitch") == "0")
    }
    assert por_cuerpo == command_policy.PIN_REQUIRED


async def test_el_catalogo_indica_que_comandos_piden_pin(db) -> None:
    client = await preparar(db)
    async with client:
        cat = (await client.get("/api/commands/catalog")).json()
    assert {c["key"] for c in cat if c["requires_pin"]} == ABREN
    assert len(cat) == len(catalog.COMMANDS)


# ───────────────────────── PIN obligatorio ─────────────────────────
@pytest.mark.parametrize("comando", sorted(ABREN))
async def test_sin_pin_no_se_envia_nada(db, send, comando) -> None:
    client = await preparar(db)
    async with client:
        r = await enviar(client, comando)                      # hay PIN guardado, pero NO basta
    assert r.status_code == 422 and r.json()["reason"] == "pin_required"
    assert send.calls == []


async def test_pin_incorrecto_se_rechaza_en_local_sin_contactar_con_chery(db, send) -> None:
    client = await preparar(db)
    async with client:
        r = await enviar(client, "desbloquear", pin="9999")
    assert r.status_code == 403 and r.json()["reason"] == "pin_invalid"
    assert send.calls == []
    (fila,) = await auditoria()
    assert fila.success is False and fila.code == "pin_invalid"
    assert "9999" not in (fila.message or "") and "9999" not in str(fila.params)


async def test_pin_correcto_envia_con_ese_pin(db, send) -> None:
    client = await preparar(db)
    async with client:
        r = await enviar(client, "desbloquear", pin=STORED_PIN)
    assert r.status_code == 200 and r.json()["success"] is True
    assert [(c["key"], c["pin"]) for c in send.calls] == [("desbloquear", STORED_PIN)]


async def test_cinco_pin_incorrectos_bloquean_tambien_el_correcto(db, send) -> None:
    client = await preparar(db)
    async with client:
        for _ in range(5):
            assert (await enviar(client, "desbloquear", pin="0000")).status_code == 403
        r = await enviar(client, "desbloquear", pin=STORED_PIN)
        assert r.status_code == 429 and r.json()["reason"] == "pin_locked" and int(r.headers["Retry-After"]) > 0
        command_policy.pin_throttle._fails.clear()
        assert (await enviar(client, "desbloquear", pin=STORED_PIN)).status_code == 200
    assert len(send.calls) == 1


async def test_un_pin_correcto_pone_a_cero_los_fallos(db, send) -> None:
    client = await preparar(db)
    async with client:
        for _ in range(4):
            await enviar(client, "desbloquear", pin="0000")
        assert (await enviar(client, "desbloquear", pin=STORED_PIN)).status_code == 200
        for _ in range(4):
            assert (await enviar(client, "desbloquear", pin="0000")).status_code == 403   # no llega a bloquear


async def test_comando_que_no_abre_no_pide_pin_y_usa_el_guardado(db, send) -> None:
    client = await preparar(db)
    async with client:
        for comando in ("bloquear", "clima_on", "maletero_cerrar", "ventanillas_cerrar", "alarma_on", "carga_iniciar"):
            assert (await enviar(client, comando)).status_code == 200, comando
    assert {c["pin"] for c in send.calls} == {STORED_PIN}


# ───────────────────────── el PIN tecleado no se queda guardado ─────────────────────────
async def test_el_pin_tecleado_vale_solo_para_ese_envio(db, send) -> None:
    client = await preparar(db, pin=None)                      # sin PIN guardado
    async with client:
        r = await enviar(client, "bloquear", pin="9999")
        assert r.status_code == 200
        r = await enviar(client, "bloquear")                   # sin PIN y sin guardado
    assert [c["pin"] for c in send.calls] == ["9999", ""]
    assert VehiclePoller.get_instance()._contexts[VIN].pin == ""


async def test_sin_pin_guardado_un_comando_que_abre_obliga_a_verificar_el_pin(db, send) -> None:
    """El taskId en caché no prueba que quien manda conozca el PIN: se descarta para forzar a Chery."""
    client = await preparar(db, pin=None)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    ctx.state.taskid, ctx.state.taskid_ts = "taskid-en-cache", 9e12
    async with client:
        assert (await enviar(client, "desbloquear", pin="4321")).status_code == 200
    assert ctx.state.taskid is None
    assert send.calls[0]["pin"] == "4321"
    assert send.calls[0]["ctx"].state is ctx.state             # comparte el anti-bloqueo del vehículo


async def test_con_pin_guardado_el_taskid_en_cache_se_conserva(db, send) -> None:
    client = await preparar(db)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    ctx.state.taskid, ctx.state.taskid_ts = "taskid-en-cache", 9e12
    async with client:
        assert (await enviar(client, "desbloquear", pin=STORED_PIN)).status_code == 200
    assert ctx.state.taskid == "taskid-en-cache"


# ───────────────────────── parámetros ─────────────────────────
async def test_params_no_pueden_convertir_un_comando_libre_en_uno_que_abre(db, send) -> None:
    client = await preparar(db)
    async with client:
        for comando, params in [("bloquear", {"lockType": "1"}),
                                ("maletero_cerrar", {"controlType": "1"}),
                                ("ventanillas_cerrar", {"controlType": "2"}),
                                ("alarma_on", {"theftAlarmSwitch": "0"}),
                                ("clima_on", {"vin": "OTRO", "taskId": "x"})]:
            r = await enviar(client, comando, params=params)
            assert r.status_code == 422 and r.json()["reason"] == "params_invalid", (comando, r.text)
    assert send.calls == []


async def test_temperatura_y_duracion_se_validan_y_normalizan(db, send) -> None:
    client = await preparar(db)
    async with client:
        assert (await enviar(client, "clima_on", params={"temperature": "21,5", "times": 20})).status_code == 200
        for malo in ({"temperature": "99"}, {"temperature": "caliente"}, {"times": "0"}, {"times": "x"}):
            assert (await enviar(client, "clima_on", params=malo)).status_code == 422, malo
    assert [c["params"] for c in send.calls] == [{"temperature": "21.5", "times": "20"}]


# ───────────────────────── errores del core ─────────────────────────
@pytest.mark.parametrize("reason,status", [("pin", 422), ("reauth", 424), ("config", 422), (None, 422)])
async def test_error_del_core_devuelve_estado_y_reason(db, send, reason, status) -> None:
    send.error = CommandError("fallo de prueba", code="A00084", reason=reason)
    client = await preparar(db)
    async with client:
        r = await enviar(client, "bloquear")
    assert r.status_code == status and r.json()["reason"] == reason and r.json()["success"] is False
    assert "fallo de prueba" in r.json()["detail"]
    (fila,) = await auditoria()
    assert fila.success is False and fila.code == "A00084"


async def test_424_no_se_confunde_con_sesion_de_evbro_caducada(db, send) -> None:
    """La web muestra el login ante un 401; una sesión Ebro caducada no debe provocarlo."""
    send.error = CommandError("Sesión caducada", reason="reauth")
    client = await preparar(db)
    async with client:
        assert (await enviar(client, "bloquear")).status_code != 401


async def test_vehiculo_desconocido_o_con_ruta_peligrosa_da_404(db, send) -> None:
    client = await preparar(db)
    async with client:
        assert (await enviar(client, "bloquear", vin="LSJA9999999999999")).status_code == 404
        assert (await enviar(client, "bloquear", vin="%2e%2e")).status_code == 404
    assert send.calls == []


def test_vehicle_data_dir_no_permite_salirse_de_data_dir(data_dir) -> None:
    from app.services.accounts import vehicle_data_dir
    for malo in ("..", "../x", "a/b", "", "ab"):
        with pytest.raises(ValueError):
            vehicle_data_dir(malo)
    assert vehicle_data_dir(VIN).startswith(str(data_dir))


# ───────────────────────── flujo del PIN de la cuenta ─────────────────────────
async def test_cambiar_el_pin_reinicia_el_antibloqueo_y_el_taskid(db, send) -> None:
    client = await preparar(db)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    for _ in range(2):
        with ctx.lockout.attempt() as intento:
            intento.record_failure()
    ctx.state.taskid = "viejo"
    assert ctx.lockout.is_locked() is True
    async with client:
        r = await client.post("/api/account/pin", json={"pin": "5678"})
    assert r.status_code == 200
    assert ctx.lockout.is_locked() is False and ctx.state.taskid is None and ctx.pin == "5678"
    async with async_session_factory() as s:
        assert (await s.execute(select(EbroAccount.pin))).scalar_one() == "5678"


async def test_borrar_el_pin_deja_vacio_el_del_contexto(db, send) -> None:
    client = await preparar(db)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    ctx.pin = STORED_PIN
    async with client:
        assert (await client.post("/api/account/pin", json={"pin": ""})).status_code == 200
    assert ctx.pin == ""


async def test_desconectar_la_cuenta_ebro_borra_el_pin_de_memoria(db, send) -> None:
    client = await preparar(db)
    ctx = VehiclePoller.get_instance()._get_or_create_ctx(VIN)
    ctx.pin = STORED_PIN
    async with client:
        assert (await client.post("/api/account/logout")).status_code == 200
    assert ctx.pin == ""


# ───────────────────────── cuándo se pide el PIN: la elección del gestor ─────────────────────────
async def _politica(client, policy):
    return await client.put("/api/account/pin-policy", json={"policy": policy})


async def test_la_politica_siempre_pide_el_pin_tambien_para_encender(db, send) -> None:
    client = await preparar(db)
    async with client:
        assert (await _politica(client, "always")).status_code == 200
        r = await enviar(client, "clima_on")
        assert r.status_code == 422 and r.json()["reason"] == "pin_required" and send.calls == []
        assert (await enviar(client, "clima_on", pin=STORED_PIN)).status_code == 200
        cat = (await client.get("/api/commands/catalog")).json()
        assert all(c["requires_pin"] for c in cat)
        assert (await client.get("/api/account/status")).json()["pin_policy"] == "always"


async def test_la_politica_nunca_abre_con_el_pin_guardado_sin_teclearlo(db, send) -> None:
    client = await preparar(db)
    async with client:
        assert (await _politica(client, "never")).status_code == 200
        for comando in ("desbloquear", "maletero_abrir", "alarma_off"):
            assert (await enviar(client, comando)).status_code == 200, comando
        assert {c["pin"] for c in send.calls} == {STORED_PIN}
        assert not any(c["requires_pin"] for c in (await client.get("/api/commands/catalog")).json())
        # back to the default: opening asks again
        assert (await _politica(client, "open")).status_code == 200
        assert (await enviar(client, "desbloquear")).json()["reason"] == "pin_required"


async def test_nunca_exige_un_pin_guardado_y_los_valores_raros_se_rechazan(db, send) -> None:
    client = await preparar(db, pin=None)
    async with client:
        r = await _politica(client, "never")
        assert r.status_code == 409 and "PIN guardado" in r.json()["detail"]
        assert (await _politica(client, "sometimes")).status_code == 422
        assert (await client.get("/api/account/status")).json()["pin_policy"] == "open"
