"""Gestor de carga con un coche simulado: lo que se manda al coche y cómo evoluciona el estado."""
from __future__ import annotations

import datetime

import httpx
import pytest

from app.config import settings
from app.db.models import AppUser, ChargePlan, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import charge_manager as cm
from app.services.charge_manager import ChargeError, ChargeManager
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000001"
UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 1, 15, 21, 0, tzinfo=UTC)       # 22:00 en España (invierno)


class Coche:
    """Sustituye al envío real: anota las órdenes y responde lo que se le diga."""
    def __init__(self):
        self.enviadas: list[tuple[str, dict, str]] = []
        self.respuesta = {"success": True, "message": "ok", "reason": None}
        self.car = {"enabled": False, "start": None, "end": None, "duration_min": None}   # lo que «lee» del coche

    async def leer(self, vin):
        return self.car

    async def __call__(self, vin, key, params, origin):
        self.enviadas.append((key, params, origin))
        return dict(self.respuesta)

    @property
    def planes(self):
        return [p["chargeAppointPlans"][0] for _k, p, _o in self.enviadas]


class Reloj:
    def __init__(self, ahora=T0): self.ahora = ahora
    def __call__(self): return self.ahora
    def avanzar(self, **kw): self.ahora += datetime.timedelta(**kw)


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setattr(settings, "TIMEZONE", "Europe/Madrid")
    VehiclePoller._latest_snapshots = {}
    yield
    VehiclePoller._latest_snapshots = {}


@pytest.fixture
async def gestor(db, monkeypatch):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        await s.commit()
    coche, reloj = Coche(), Reloj()
    g = ChargeManager(sender=coche, clock=reloj, reader=coche.leer, verify_delay_s=None)
    g.coche, g.reloj = coche, reloj
    monkeypatch.setattr(ChargeManager, "_instance", g)
    return g


async def fila() -> ChargePlan:
    async with async_session_factory() as s:
        return await s.get(ChargePlan, VIN)


def lectura(soc, cargando=True, enchufado=True):
    return {"battery_level": soc, "is_charging": cargando, "cable_connected": enchufado}


# ───────────────────────── acciones del usuario ─────────────────────────
async def test_parar_carga_manda_una_ventana_ya_terminada(gestor) -> None:
    estado = await gestor.stop(VIN)
    (key, params, origin), = gestor.coche.enviadas
    plan = params["chargeAppointPlans"][0]
    assert key == "carga_prog_on" and params["mainSwitch"] == 1
    assert (plan["startTime"], plan["timeConsuming"]) == (19 * 60 + 30, 60)       # 21:00 UTC − 90 min
    assert estado["mode"] == "hold" and estado["hold_reason"] == "user" and estado["status"] == "hold_user"
    assert estado["schedule"]["enabled"] is False


async def test_iniciar_carga_quita_la_programacion(gestor) -> None:
    await gestor.stop(VIN)
    estado = await gestor.start(VIN)
    key, params, _ = gestor.coche.enviadas[-1]
    assert key == "carga_prog_off" and params["mainSwitch"] == 0 and params["chargeAppointPlans"][0]["switchStatus"] == 0
    assert estado["mode"] == "free" and estado["hold_reason"] is None and estado["status"] == "free"


async def test_programar_con_inicio_y_fin(gestor) -> None:
    estado = await gestor.set_schedule(VIN, 22 * 60, 8 * 60)                       # 22:00 → 06:00
    plan = gestor.coche.planes[-1]
    assert plan["startTime"] == 21 * 60 and plan["timeConsuming"] == 480 and plan["switchStatus"] == 1   # 22:00 local = 21:00 UTC
    assert estado["mode"] == "scheduled" and estado["schedule"] == {"enabled": True, "start": "22:00", "end": "06:00", "duration_min": 480}


@pytest.mark.parametrize("duracion,razon", [(30, "duration_too_short"), (1440, "duration_too_long")])
async def test_programar_valida_la_duracion(gestor, duracion, razon) -> None:
    with pytest.raises(ChargeError) as e:
        await gestor.set_schedule(VIN, 0, duracion)
    assert e.value.reason == razon and gestor.coche.enviadas == []


async def test_quitar_la_programacion(gestor) -> None:
    await gestor.set_schedule(VIN, 22 * 60, 480)
    estado = await gestor.clear_schedule(VIN)
    assert gestor.coche.enviadas[-1][0] == "carga_prog_off"
    assert estado["mode"] == "free" and estado["schedule"]["enabled"] is False
    assert estado["schedule"]["start"] == "22:00"                                  # se recuerda para la próxima vez


async def test_quitar_la_programacion_no_levanta_una_pausa(gestor) -> None:
    await gestor.stop(VIN)
    n = len(gestor.coche.enviadas)
    estado = await gestor.clear_schedule(VIN)
    assert estado["mode"] == "hold" and len(gestor.coche.enviadas) == n


async def test_parar_y_iniciar_devuelve_la_programacion_del_usuario(gestor) -> None:
    await gestor.set_schedule(VIN, 22 * 60, 480)
    estado = await gestor.stop(VIN)
    assert estado["mode"] == "hold" and estado["schedule"]["enabled"] is True        # se guarda, no se pierde
    estado = await gestor.start(VIN)
    key, params, _ = gestor.coche.enviadas[-1]
    plan = params["chargeAppointPlans"][0]
    assert key == "carga_prog_on" and (plan["startTime"], plan["timeConsuming"], plan["switchStatus"]) == (21 * 60, 480, 1)
    assert estado["mode"] == "scheduled" and estado["schedule"]["start"] == "22:00"


async def test_parar_guarda_la_programacion_que_tenia_el_coche_y_iniciar_la_devuelve(gestor) -> None:
    gestor.coche.car = {"enabled": True, "start": "01:00", "end": "05:00", "duration_min": 240}   # puesta desde la app oficial
    await gestor.stop(VIN)
    estado = await gestor.start(VIN)
    plan = gestor.coche.planes[-1]
    assert (plan["startTime"], plan["timeConsuming"]) == (0, 240)                                   # 01:00 local = 00:00 UTC
    assert estado["mode"] == "scheduled" and estado["schedule"] == {"enabled": True, "start": "01:00", "end": "05:00", "duration_min": 240}


async def test_parar_y_iniciar_sin_programacion_previa_apaga_la_programacion(gestor) -> None:
    await gestor.stop(VIN)
    estado = await gestor.start(VIN)
    assert gestor.coche.enviadas[-1][0] == "carga_prog_off" and estado["schedule"]["enabled"] is False


async def test_parar_dos_veces_no_confunde_la_ventana_de_pausa_con_una_programacion(gestor) -> None:
    await gestor.stop(VIN)
    gestor.coche.car = {"enabled": True, "start": "20:30", "end": "21:30", "duration_min": 60}   # la ventana de pausa
    await gestor.stop(VIN)
    estado = await gestor.start(VIN)
    assert estado["mode"] == "free" and estado["schedule"]["enabled"] is False


async def test_quitar_una_programacion_creada_fuera_de_evbro(gestor) -> None:
    estado = await gestor.clear_schedule(VIN)
    assert gestor.coche.enviadas[-1][0] == "carga_prog_off" and estado["mode"] == "free"


# ───────────────────────── verificación de que el coche lo aplicó ─────────────────────────
async def test_verificar_reenvia_una_vez_si_el_coche_no_aplico_el_quitar(gestor) -> None:
    await gestor.stop(VIN)
    await gestor.start(VIN)
    n = len(gestor.coche.enviadas)
    gestor.coche.car = {"enabled": True, "start": "20:30", "end": "21:30", "duration_min": 60}   # sigue con la ventana
    assert await gestor.verify_applied(VIN) is False
    assert len(gestor.coche.enviadas) == n + 1 and gestor.coche.enviadas[-1][0] == "carga_prog_off"
    assert gestor.coche.enviadas[-1][2] == "automático (verificación)"
    gestor.coche.car = {"enabled": False, "start": None, "end": None, "duration_min": None}
    assert await gestor.verify_applied(VIN) is True and len(gestor.coche.enviadas) == n + 1


async def test_verificar_no_toca_nada_si_no_se_puede_leer_o_coincide(gestor) -> None:
    await gestor.stop(VIN)
    n = len(gestor.coche.enviadas)
    gestor.coche.car = None
    assert await gestor.verify_applied(VIN) is True
    gestor.coche.car = {"enabled": True, "start": "20:30", "end": "21:30", "duration_min": 60}   # pausa aplicada: coincide
    assert await gestor.verify_applied(VIN) is True and len(gestor.coche.enviadas) == n


async def test_estado_marca_aplicandose_mientras_el_coche_no_refleja_la_orden(gestor) -> None:
    await gestor.stop(VIN)
    await gestor.start(VIN)
    gestor.coche.car = {"enabled": True, "start": "20:30", "end": "21:30", "duration_min": 60}
    assert (await gestor.state(VIN, live=True))["car_pending"] is True
    gestor.reloj.avanzar(minutes=5)
    assert (await gestor.state(VIN, live=True))["car_pending"] is False        # pasado un rato ya no es «aplicándose»


# ───────────────────────── fallos ─────────────────────────
async def test_si_el_coche_rechaza_el_estado_no_cambia_y_se_explica(gestor) -> None:
    gestor.coche.respuesta = {"success": False, "message": "PIN no configurado", "reason": "pin"}
    with pytest.raises(ChargeError) as e:
        await gestor.stop(VIN)
    assert e.value.reason == "pin" and e.value.status == 422
    row = await fila()
    assert row.mode == "free" and "PIN" in row.last_error
    assert (await gestor.state(VIN))["last_error"] == row.last_error


async def test_sesion_ebro_caducada_es_424(gestor) -> None:
    gestor.coche.respuesta = {"success": False, "message": "Sesión caducada", "reason": "reauth"}
    with pytest.raises(ChargeError) as e:
        await gestor.start(VIN)
    assert e.value.status == 424 and e.value.reason == "reauth"


# ───────────────────────── objetivo de batería ─────────────────────────
async def test_el_objetivo_solo_admite_los_valores_del_desplegable(gestor) -> None:
    with pytest.raises(ChargeError) as e:
        await gestor.set_target(VIN, 52)
    assert e.value.reason == "target_invalid"
    assert (await gestor.set_target(VIN, 80))["target_pct"] == 80


async def test_sin_haber_tocado_nada_el_gestor_no_manda_nunca_nada(gestor) -> None:
    await gestor.on_snapshot(VIN, lectura(100))
    await gestor.tick()
    assert gestor.coche.enviadas == [] and await fila() is None             # ni órdenes ni filas nuevas


async def test_poner_un_objetivo_solo_guarda_hasta_que_se_alcance(gestor) -> None:
    await gestor.set_target(VIN, 80)
    await gestor.on_snapshot(VIN, lectura(60))
    assert gestor.coche.enviadas == []


async def test_al_alcanzar_el_objetivo_cargando_se_pausa_y_se_pide_sondeo_fino(gestor) -> None:
    await gestor.set_target(VIN, 80)
    await gestor.on_snapshot(VIN, lectura(81))
    assert len(gestor.coche.enviadas) == 1 and gestor.coche.enviadas[0][2] == "automático"
    estado = await gestor.state(VIN)
    assert estado["mode"] == "hold" and estado["hold_reason"] == "target" and estado["status"] == "hold_target"
    assert gestor.wants_fast_poll(VIN) is True


async def test_se_libera_al_enchufar_con_hueco_y_vuelve_a_carga_libre(gestor) -> None:
    await gestor.set_target(VIN, 80)
    await gestor.on_snapshot(VIN, lectura(80))
    gestor.reloj.avanzar(hours=14)
    await gestor.on_snapshot(VIN, lectura(45, cargando=False, enchufado=True))
    assert gestor.coche.enviadas[-1][0] == "carga_prog_off"                        # sin programación del usuario → carga ya
    estado = await gestor.state(VIN)
    assert estado["mode"] == "free" and gestor.wants_fast_poll(VIN) is False


async def test_se_libera_volviendo_a_la_programacion_del_usuario(gestor) -> None:
    await gestor.set_schedule(VIN, 22 * 60, 480)
    await gestor.set_target(VIN, 80)
    await gestor.on_snapshot(VIN, lectura(80))
    assert (await gestor.state(VIN))["mode"] == "hold"
    gestor.reloj.avanzar(hours=14)
    await gestor.on_snapshot(VIN, lectura(40, cargando=False))
    key, params, _ = gestor.coche.enviadas[-1]
    assert key == "carga_prog_on" and params["chargeAppointPlans"][0]["timeConsuming"] == 480
    assert (await gestor.state(VIN))["mode"] == "scheduled"


async def test_subir_el_objetivo_a_100_libera_la_pausa(gestor) -> None:
    await gestor.set_target(VIN, 80)
    await gestor.on_snapshot(VIN, lectura(82))
    estado = await gestor.set_target(VIN, 100)
    assert estado["mode"] == "free" and gestor.coche.enviadas[-1][0] == "carga_prog_off"


async def test_las_acciones_automaticas_se_espacian_tambien_tras_un_fallo(gestor) -> None:
    await gestor.set_target(VIN, 80)
    gestor.coche.respuesta = {"success": False, "message": "sin red", "reason": None}
    await gestor.on_snapshot(VIN, lectura(85))                                    # intenta pausar y falla (no lanza)
    await gestor.on_snapshot(VIN, lectura(85))                                    # 0 min después: no reintenta
    assert len(gestor.coche.enviadas) == 1
    gestor.reloj.avanzar(minutes=cm.MIN_AUTO_GAP_MIN + 1)
    await gestor.on_snapshot(VIN, lectura(85))
    assert len(gestor.coche.enviadas) == 2


# ───────────────────────── renovar la pausa ─────────────────────────
async def test_la_pausa_se_renueva_sola_antes_de_que_se_reabra(gestor, monkeypatch) -> None:
    monkeypatch.setattr(cm, "TICK_EVERY_S", 0)
    await gestor.stop(VIN)
    primero = gestor.coche.planes[-1]["startTime"]
    gestor.reloj.avanzar(hours=10)
    await gestor.tick()
    assert len(gestor.coche.enviadas) == 1                                        # a 12 h de abrirse: no hace falta
    gestor.reloj.avanzar(hours=9)                                                 # quedan 3 h 30
    await gestor.tick()
    assert len(gestor.coche.enviadas) == 2
    segundo = gestor.coche.planes[-1]["startTime"]
    assert segundo != primero
    row = await fila()
    assert row.mode == "hold" and row.hold_reason == "user" and row.hold_start_utc == segundo


async def test_renovar_conserva_el_motivo_de_la_pausa(gestor, monkeypatch) -> None:
    monkeypatch.setattr(cm, "TICK_EVERY_S", 0)
    await gestor.set_target(VIN, 80)
    await gestor.on_snapshot(VIN, lectura(80))
    gestor.reloj.avanzar(hours=19)
    await gestor.tick()
    assert (await fila()).hold_reason == "target"


# ───────────────────────── API ─────────────────────────
class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


async def _web(gestor):
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


async def test_api_objetivo_parar_iniciar(gestor) -> None:
    async with await _web(gestor) as c:
        r = await c.put(f"/api/vehicles/{VIN}/charge/target", json={"target_pct": 80})
        assert r.status_code == 200 and r.json()["target_pct"] == 80 and r.json()["target_choices"] == list(range(50, 101, 5))
        bad = await c.put(f"/api/vehicles/{VIN}/charge/target", json={"target_pct": 52})
        assert bad.status_code == 422 and bad.json()["reason"] == "target_invalid"
        r = await c.post(f"/api/vehicles/{VIN}/charge/stop")
        assert r.status_code == 200 and r.json()["status"] == "hold_user"
        r = await c.post(f"/api/vehicles/{VIN}/charge/start")
        assert r.status_code == 200 and r.json()["status"] == "free"
        assert (await c.get(f"/api/vehicles/{VIN}/charge/plan")).json()["target_pct"] == 80


async def test_api_programar_con_fin_o_con_duracion(gestor) -> None:
    async with await _web(gestor) as c:
        url = f"/api/vehicles/{VIN}/charge/schedule"
        r = await c.put(url, json={"start": "23:30", "end": "07:30"})
        assert r.status_code == 200 and r.json()["schedule"] == {"enabled": True, "start": "23:30", "end": "07:30", "duration_min": 480}
        r = await c.put(url, json={"start": "01:00", "duration_min": 180})
        assert r.json()["schedule"]["end"] == "04:00"
        r = await c.delete(url)
        assert r.status_code == 200 and r.json()["schedule"]["enabled"] is False


@pytest.mark.parametrize("cuerpo,razon", [
    ({"start": "25:00", "end": "06:00"}, "time_invalid"),
    ({"start": "22:00"}, "schedule_invalid"),
    ({"start": "22:00", "end": "06:00", "duration_min": 120}, "schedule_invalid"),
    ({"start": "22:00", "end": "22:00"}, "schedule_invalid"),
    ({"start": "22:00", "duration_min": 30}, "duration_too_short"),
])
async def test_api_rechaza_programaciones_invalidas_sin_tocar_el_coche(gestor, cuerpo, razon) -> None:
    async with await _web(gestor) as c:
        r = await c.put(f"/api/vehicles/{VIN}/charge/schedule", json=cuerpo)
    assert r.status_code == 422 and r.json()["reason"] == razon and gestor.coche.enviadas == []


async def test_api_error_del_coche_y_vehiculo_desconocido(gestor) -> None:
    gestor.coche.respuesta = {"success": False, "message": "Sesión caducada", "reason": "reauth"}
    async with await _web(gestor) as c:
        r = await c.post(f"/api/vehicles/{VIN}/charge/stop")
        assert r.status_code == 424 and r.json()["reason"] == "reauth" and r.json()["success"] is False
        assert (await c.post("/api/vehicles/LSJA9999999999999/charge/stop")).status_code == 404
