"""Canal MQTT del coche: conexión, mensajes, disparadores de sondeo y guarda anti-bucle."""
from __future__ import annotations

import asyncio
import json
import pathlib

import httpx
import pytest

from app.config import settings
from app.core.context import CoreCtx
from app.core.telemetry import parse_telemetry_data
from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import car_events as ce
from app.services.poller import VehiclePoller
from app.vehicle.mqtt_client import MqttConnectError

VIN = "LSJA0000000000001"
FIELDS = json.loads((pathlib.Path(__file__).parent / "vehicle" / "fixtures" / "mqtt_5a02.json").read_text())


def payload(data: dict, service_type: str = "5A02") -> bytes:
    return json.dumps({"content": {"serviceType": service_type, "data": data}}).encode()


class FakeClient:
    """Sustituye al cliente paho: guarda los callbacks para que el test los dispare."""
    instances: list["FakeClient"] = []
    connect_error: Exception | None = None

    def __init__(self, config, *, on_message, on_connected, on_disconnected, on_subscribed):
        self.config = config
        self.on_message, self.on_connected = on_message, on_connected
        self.on_disconnected, self.on_subscribed = on_disconnected, on_subscribed
        self.disconnected = False
        FakeClient.instances.append(self)

    def connect(self):
        if FakeClient.connect_error:
            raise FakeClient.connect_error

    def disconnect(self):
        self.disconnected = True


class StubPoller:
    """Lo que el servicio necesita del poller, anotando las llamadas."""
    def __init__(self):
        self._contexts = {VIN: CoreCtx(vin=VIN)}
        self.applied, self.polls, self.events = [], [], []

    def apply_car_fields(self, vin, fields, geo=None):
        self.applied.append((vin, dict(fields), geo))

    def request_poll(self, vin, force=False):
        self.polls.append((vin, force))

    def broadcast_event(self, payload):
        self.events.append(payload)


@pytest.fixture(autouse=True)
def _entorno(monkeypatch, data_dir):
    FakeClient.instances, FakeClient.connect_error = [], None
    monkeypatch.setattr(settings, "CAR_MQTT_ENABLED", True)
    monkeypatch.setattr(settings, "EBRO_VIN", None)
    monkeypatch.setattr(ce.certificates, "provision", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(ce, "_bff_login", lambda ctx: ("usertoken", "T123"))
    monkeypatch.setattr(ce.CarEventService, "_instance", None)
    monkeypatch.setattr(ce.CarEventHub, "_instance", None)


async def flush():
    await asyncio.sleep(0.02)          # deja correr los call_soon_threadsafe pendientes


async def arrancar(poller=None):
    poller = poller or StubPoller()
    svc = ce.CarEventService(client_factory=FakeClient)
    await svc.start(poller)
    return svc, poller


async def conectado():
    svc, poller = await arrancar()
    client = FakeClient.instances[0]
    client.on_connected(True, 0)
    client.on_subscribed(True, "1", svc._topic)
    await flush()
    return svc, poller, client


# ───────────────────────── arranque y estados ─────────────────────────
async def test_desactivado_por_defecto(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CAR_MQTT_ENABLED", False)
    svc, _ = await arrancar()
    assert svc.state == "disabled" and FakeClient.instances == []
    assert svc.status()["enabled"] is False


async def test_sin_vehiculos_no_conecta() -> None:
    poller = StubPoller()
    poller._contexts = {}
    svc, _ = await arrancar(poller)
    assert svc.state == "no_account" and FakeClient.instances == []


async def test_conecta_con_el_tuserid_de_la_cuenta() -> None:
    svc, _ = await arrancar()
    assert svc.state == "connecting"
    cfg = FakeClient.instances[0].config
    assert cfg.tuserid == "T123" and cfg.topic == "app/4/T123/account/msgCenter/msg"
    assert cfg.certs_dir.endswith("/certs")


async def test_estados_conectado_y_suscrito() -> None:
    svc, _, client = await conectado()
    assert svc.state == "subscribed" and "app/4/T123" in svc.detail
    client.on_subscribed(False, "128", svc._topic)
    await flush()
    assert svc.state == "rejected" and "propietaria" in svc.detail


async def test_sin_certificados_no_llega_a_pedir_el_token(monkeypatch) -> None:
    monkeypatch.setattr(ce.certificates, "provision", lambda *a, **k: (False, "faltan ca.pem"))
    llamado = []
    monkeypatch.setattr(ce, "_bff_login", lambda ctx: llamado.append(1) or ("u", "t"))
    svc, _ = await arrancar()
    assert svc.state == "no_certs" and "faltan ca.pem" in svc.detail and not llamado and not FakeClient.instances


async def test_sesion_ebro_caducada(monkeypatch) -> None:
    monkeypatch.setattr(ce, "_bff_login", lambda ctx: (None, None))
    svc, _ = await arrancar()
    assert svc.state == "no_account" and "caducado" in svc.detail


async def test_fallo_de_conexion_inicial(monkeypatch) -> None:
    FakeClient.connect_error = MqttConnectError("DNS")
    svc, _ = await arrancar()
    assert svc.state == "error" and "DNS" in svc.detail


async def test_un_error_inesperado_no_tumba_el_arranque(monkeypatch) -> None:
    monkeypatch.setattr(ce, "_bff_login", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
    svc, _ = await arrancar()
    assert svc.state == "error" and "boom" in svc.detail


# ───────────────────────── mensajes ─────────────────────────
async def test_un_5a02_actualiza_la_telemetria() -> None:
    svc, poller, client = await conectado()
    client.on_message(payload({"trunkDoor": "1", "doorLock": "0"}), svc._topic)
    await flush()
    assert poller.applied == [(VIN, {"trunkDoor": "1", "doorLock": "0"}, None)]
    assert svc.messages == 1 and svc.status()["awake"] is True
    assert svc.fields_for(VIN)["trunkDoor"] == "1" and svc.fields_for("OTRO") == {}


async def test_el_latido_de_marcha_no_es_telemetria() -> None:
    svc, poller, client = await conectado()
    client.on_message(payload({"time": "1730000000"}), svc._topic)
    await flush()
    assert poller.applied == [] and svc.messages == 1


async def test_un_topic_desconocido_se_ignora() -> None:
    svc, poller, client = await conectado()
    client.on_message(payload({"trunkDoor": "1"}), "app/4/OTRO/loquesea")
    await flush()
    assert poller.applied == [] and svc.messages == 0


async def test_basura_en_el_payload_no_rompe_nada() -> None:
    svc, poller, client = await conectado()
    for malo in (b"", b"no es json", b"[]", b'{"content": 5}'):
        client.on_message(malo, svc._topic)
    await flush()
    assert poller.applied == []


async def test_despertar_pide_una_sonda_sin_forzar_y_solo_una_vez() -> None:
    svc, poller, client = await conectado()
    client.on_message(payload({"doorLock": "0"}), svc._topic)
    client.on_message(payload({"trunkDoor": "0"}), svc._topic)
    await flush()
    assert poller.polls == [(VIN, False)]


async def test_la_rafaga_de_marcha_fuerza_una_sonda_una_vez() -> None:
    svc, poller, client = await conectado()
    for _ in range(8):
        client.on_message(payload({"engineState": "1"}), svc._topic)
    await flush()
    assert poller.polls.count((VIN, True)) == 1
    assert svc.status()["moving"] is True


async def test_enchufar_o_desenchufar_el_cable_fuerza_una_sonda() -> None:
    svc, poller, client = await conectado()
    client.on_message(payload({"chargeGunState": "0"}), svc._topic)       # primera lectura: no es un cambio
    await flush()
    assert (VIN, True) not in poller.polls
    client.on_message(payload({"chargeGunState": "1"}), svc._topic)
    await flush()
    assert poller.polls.count((VIN, True)) == 1
    client.on_message(payload({"chargeGunState": "1"}), svc._topic)       # igual que antes: nada
    await flush()
    assert poller.polls.count((VIN, True)) == 1


async def test_la_confirmacion_de_un_comando_llega_a_la_web_y_no_es_telemetria() -> None:
    svc, poller, client = await conectado()
    client.on_message(payload({"result": "1", "seq": f"{VIN}-1", "resultTime": "x"}, "110D"), svc._topic)
    await flush()
    assert poller.applied == []
    (evento,) = poller.events
    assert evento["type"] == "command_result" and evento["applied"] is True and "confirmado" in evento["message"]
    client.on_message(payload({"result": "9", "seq": "x", "reason": ["door open"]}, "110D"), svc._topic)
    await flush()
    assert poller.events[-1]["failed"] is True


async def test_si_algun_dia_llega_posicion_se_pasa_al_poller() -> None:
    svc, poller, client = await conectado()
    client.on_message(payload({"lat": "41.38", "lon": "2.17", "speed": "0", "extra": "x"}, "1301"), svc._topic)
    await flush()
    assert poller.applied[-1][2] == {"lat": "41.38", "lon": "2.17", "speed": "0"}


# ───────────────────────── guarda anti-bucle (choque de clientId) ─────────────────────────
async def test_conexiones_que_duran_segundos_paran_el_servicio() -> None:
    svc, _ = await arrancar()
    client = FakeClient.instances[0]
    for _ in range(ce.FLAP_MAX_EPHEMERAL):
        client.on_connected(True, 0)
        client.on_disconnected(7)             # otro cliente nos echa al instante
        await flush()
    assert svc.state == "conflict" and "Home Assistant" in svc.detail
    await asyncio.sleep(0.05)
    assert client.disconnected is True


async def test_una_desconexion_tras_mucho_tiempo_no_es_un_conflicto(monkeypatch) -> None:
    svc, _, client = await conectado()
    svc.connected_since -= 3600               # llevaba una hora conectado
    client.on_disconnected(0)
    await flush()
    assert svc.state == "connecting" and svc._ephemeral == []


async def test_rechazos_repetidos_del_broker_tambien_paran_el_servicio() -> None:
    svc, _ = await arrancar()
    client = FakeClient.instances[0]
    for _ in range(ce.FLAP_MAX_EPHEMERAL):
        client.on_connected(False, 5)
        await flush()
    assert svc.state == "conflict"


async def test_restart_limpia_el_conflicto() -> None:
    svc, poller = await arrancar()
    client = FakeClient.instances[0]
    for _ in range(ce.FLAP_MAX_EPHEMERAL):
        client.on_connected(False, 5)
        await flush()
    assert svc.state == "conflict"
    await svc.restart(poller)
    assert svc.state == "connecting" and len(FakeClient.instances) == 2


# ───────────────────────── integración con el poller ─────────────────────────
async def test_el_push_se_funde_sobre_la_ultima_lectura_y_avisa_por_websocket(db) -> None:
    poller = VehiclePoller()
    poller._latest_snapshots = {VIN: parse_telemetry_data({"dumpEnergy": "80", "trunkDoor": "0", "doorLock": "1"})}
    recibido = []
    poller._ws_subscribers = {recibido.append}
    nuevo = poller.apply_car_fields(VIN, {"trunkDoor": "1"})
    assert nuevo["doors"]["trunk"] and nuevo["battery_level"] == 80.0        # conserva la batería de la sonda
    assert recibido[0]["type"] == "telemetry" and recibido[0]["data"]["doors"]["trunk"]


async def test_la_sonda_respeta_el_ultimo_push(db, monkeypatch) -> None:
    """La sonda puede traer una foto vieja de la nube: el push manda cuando ambos traen la clave."""
    from app.services import poller as poller_module
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        await s.commit()
    servicio = ce.CarEventService(client_factory=FakeClient)
    servicio.vin = VIN
    servicio.vehicle_state.record_message({"trunkDoor": "1"}, 1.0)
    ce.CarEventHub.get_instance()._services[1] = servicio              # the service of car's owner (manager 1)

    def sonda_falsa(ctx, publish, force=False, on_data=None):
        on_data({"dumpEnergy": "55", "trunkDoor": "0"})          # la nube aún dice «cerrado»
        return {"ok": True, "online": True, "got_data": True}
    monkeypatch.setattr(poller_module, "probe_once", sonda_falsa)

    poller = VehiclePoller()
    poller._contexts = {VIN: CoreCtx(vin=VIN)}
    telemetria = await poller.poll_vehicle(VIN, force=True)
    assert telemetria["doors"]["trunk"] == 1 and telemetria["battery_level"] == 55.0


async def test_request_poll_no_apila_sondas_para_el_mismo_vehiculo(monkeypatch) -> None:
    poller = VehiclePoller()
    poller._poll_requests = {}
    llamadas = []

    async def lenta(vin, force=False):
        llamadas.append((vin, force))
        await asyncio.sleep(0.05)
    monkeypatch.setattr(poller, "poll_vehicle", lenta)
    poller.request_poll(VIN, force=False)
    poller.request_poll(VIN, force=True)           # ya hay una en curso
    await asyncio.sleep(0.1)
    assert llamadas == [(VIN, False)]
    poller.request_poll(VIN, force=True)
    await asyncio.sleep(0.1)
    assert llamadas == [(VIN, False), (VIN, True)]


# ───────────────────────── API ─────────────────────────
async def test_la_api_de_estado_exige_sesion_y_responde(db) -> None:
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()

        class Req:
            client = type("C", (), {"host": "127.0.0.1"})()
            headers = {"user-agent": "t"}
        token = await auth.create_session(s, user, Req())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get("/api/car-events/status")).status_code == 401
        c.cookies.set(auth.COOKIE_NAME, token)
        r = await c.get("/api/car-events/status")
        assert r.status_code == 200 and {"enabled", "state", "detail", "messages", "awake"} <= set(r.json())
        assert (await c.post("/api/car-events/reconnect")).status_code == 200
